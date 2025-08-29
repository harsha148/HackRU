import re
import certifi
import pickle
import os
import base64
import json

from bson import json_util
from fastapi import FastAPI, Request, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from bson import ObjectId
from openai import OpenAI
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request as GoogleRequest
from bs4 import BeautifulSoup
from dotenv import load_dotenv

app = FastAPI(redirect_slashes=False)
load_dotenv()

# MongoDB Setup
MONGO_URI = "mongodb+srv://sasank:sasank@mongodbcluster.trg7krh.mongodb.net/?retryWrites=true&w=majority&appName=MongoDBCluster"
client = AsyncIOMotorClient(MONGO_URI, tlsCAFile=certifi.where())
db = client.job_tracker


# User Model
class User(BaseModel):
    name: str
    email: str
    last_tracked: Optional[datetime] = None

# Email Model
class Email(BaseModel):
    user_id: str
    job_id: str
    subject: str
    body: str


# Job Model
class Job(BaseModel):
    user_id: str
    company: str
    position: str
    status: str
    applied_date: Optional[datetime] = None
    last_update: Optional[datetime] = None


@app.post("/register")
async def add_user(user: User):
    existing_user = await db.users.find_one({"email": user.email})
    if not existing_user:
        user.last_tracked = datetime.now() - timedelta(days=3)
        result = await db.users.insert_one(user.dict())
        new_user = await db.users.find_one({"email": result.inserted_id})
        return {"user": new_user, "message": "User registered"}
    return {"user": user, "message": "User already exists"}


# Create Job
@app.post("/jobs")
async def add_job(job: Job):
    job.last_update = job.applied_date
    new_job = await db.jobs.insert_one(job.dict())
    return {"id": str(new_job.inserted_id), "message": "Job added"}


@app.get("/jobs/{user_id}", response_model=List[Job])
async def get_jobs(user_id: str, emails):
    user = await db.users.find_one({"email": user_id})

    last_tracked = user.get("last_tracked")

    for email in emails:
        internal_date_dt = datetime.fromtimestamp(int(email['internal_date'])/1000)
        if internal_date_dt > last_tracked:
            email_type = classify_email(email['subject'])
            if email_type == "job":
                new_job = json.loads(extract_job_details(email['body']))
                job_obj = {"user_id": user.get('email'), "status": new_job['status'],"position": new_job['position'],"company":new_job["company"]}
                job = Job(**job_obj)
                if job.status == 'APPLIED':
                    job.applied_date = internal_date_dt
                job.last_update = internal_date_dt
                job_save = await db.jobs.insert_one(job.dict())
                job_id = job_save.inserted_id
                email_obj = {"user_id": user.get('email'), "job_id": str(job_id), "subject": email['subject'], "body": email['body']}
                email = Email.parse_obj(email_obj)
                await db.emails.insert_one(email.dict())

    jobs = await db.jobs.find({
        "user_id": user_id,
    }).to_list(length=1000)

    now = datetime.now()
    await db.users.update_one({"email": user_id}, {"$set": {"last_tracked": now}})

    return jobs


@app.get("/read_gmail")
async def read_gmail(request: Request):
    emailId = request.query_params.get("userId")

    jobs = await db.jobs.find({
        "user_id": emailId
    }).to_list(length=1000)

    jobs_json = json.loads(json_util.dumps(jobs))

    return jobs_json


@app.put("/jobs/{job_id}")
async def update_job(job_id: str, status: str):
    update_result = await db.jobs.update_one({"_id": ObjectId(job_id)}, {"$set": {"status": status, "last_update": datetime.utcnow()}})
    if update_result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"message": "Job updated"}


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    delete_result = await db.jobs.delete_one({"_id": ObjectId(job_id)})
    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"message": "Job deleted"}


SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/userinfo.profile']
CLIENT_SECRETS_FILE = "credentials.json"
TOKEN_PICKLE_FILE = "token.pickle"


flow = Flow.from_client_secrets_file(
    CLIENT_SECRETS_FILE,
    scopes=SCOPES,
    redirect_uri="http://localhost:8000/callback"
)


@app.get("/login")
async def login():
    authorization_url, state = flow.authorization_url()
    return RedirectResponse(url=authorization_url)


@app.get("/callback")
async def callback(request: Request):
    code = request.query_params.get("code")
    flow.fetch_token(code=code)
    credentials = flow.credentials

    with open(TOKEN_PICKLE_FILE, 'wb') as token:
        pickle.dump(credentials, token)
    res = get_email()
    emails, email, name = res["emails"], res["emailId"], res["name"]
    user_obj = {"email": email, "name": name}
    user = User.parse_obj(user_obj)
    await add_user(user)
    jobs = await get_jobs(email, emails)
    redirect_uri = f"http://localhost:5002?userId={email}"
    return RedirectResponse(url=redirect_uri,status_code=302)


def extract_subject_and_body(service, message_id):
    message = service.users().messages().get(userId='me', id=message_id, format='full').execute()
    subject = None
    body = None
    internal_date = None

    # Extract subject
    for header in message['payload']['headers']:
        if header['name'] == 'Subject':
            subject = header['value']

    internal_date = message['internalDate']

    payload = message['payload']

    def extract_body_from_parts(parts):
        nonlocal body
        for part in parts:
            if part['mimeType'] == 'text/plain':
                if 'body' in part and 'data' in part['body']:
                    decoded_body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                    body = decoded_body
            elif part['mimeType'] == 'text/html':
                if 'body' in part and 'data' in part['body']:
                    decoded_body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                    # Remove HTML tags
                    soup = BeautifulSoup(decoded_body, 'html.parser')
                    body = soup.get_text()
            elif part['mimeType'] == 'multipart/alternative':
                extract_body_from_parts(part['parts'])
            elif part['mimeType'] == 'multipart/related':
                extract_body_from_parts(part['parts'])
            elif part['mimeType'] == 'multipart/mixed':
                extract_body_from_parts(part['parts'])

    if 'parts' in payload:
        extract_body_from_parts(payload['parts'])
    else:
        if 'body' in payload and 'data' in payload['body']:
            decoded_body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')

            if '<' in decoded_body and '>' in decoded_body:
                soup = BeautifulSoup(decoded_body, 'html.parser')
                body = soup.get_text()
            else:
                body = decoded_body
        else:
            raw_message = service.users().messages().get(userId='me', id=message_id, format='raw').execute()
            raw_message_string = base64.urlsafe_b64decode(raw_message['raw']).decode('utf-8')
            body = raw_message_string
    if body:
        body = clean_text(body)
    return subject, body, internal_date


def clean_text(text):
    text = text.strip()
    text = re.sub(r'[\n\t\r]+', ' ', text)
    text = re.sub(r'[\u200c\u2007\u200b\u2028\u2029]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^ -~]+', '', text)

    return text


def get_email():
    if os.path.exists(TOKEN_PICKLE_FILE):
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            credentials = pickle.load(token)
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(GoogleRequest())

        service = build('gmail', 'v1', credentials=credentials)
        emailId = service.users().getProfile(userId='me').execute()['emailAddress']
        people_service = build('people', "v1", credentials=service._http.credentials)
        name = people_service.people().get(resourceName="people/me", personFields="names,emailAddresses").execute()['names'][0]['displayName']
        results = service.users().messages().list(userId='me').execute()
        messages = results.get('messages', [])

        email_data = []
        for message in messages:
            subject, body, internal_date = extract_subject_and_body(service, message['id'])
            email_data.append({
                "id": message['id'],
                "subject": subject,
                "body": body,
                "internal_date": internal_date
            })

        return {"emails": email_data, "emailId": emailId, "name": name}
    else:
        return "No credentials found."


def extract_job_details(email_body: str):
    """Use GPT-4 to extract job details (company, position, status) from a job application email."""

    prompt = f"""
        Extract structured job details from the following email content. Identify:

        - *Company Name* (Example: Google, Amazon, Microsoft)
        - *Job Position* (Example: Software Engineer Intern, Data Scientist). If multiple job positions are mentioned, extract the most relevant one.
        - *Application Status* (One of: Applied, Interview, Rejected, Offer)

        *Guidelines for extracting the Job Position:*
        - The job title might appear near phrases like "Your application for", "applied for", "position", or "role".
        - If found in a list or within a sentence, extract the *most relevant position*.
        - If the job title appears with a *location or reference number*, extract only the title.
        - If the job position is missing, return "Unknown" instead of null.

        *Application Status Classification Rules:*
        - *Applied* → If the email confirms receipt of an application.
        - *Interview* → If the email invites the candidate for an interview.
        - *Rejected* → If the email states that the application was not selected.
        - *Offer* → If the email confirms an offer.

        *Examples:*

        1. *Email:* "Your application for Distributed Systems Engineering - Intern has been received."  
           *Output:* {{"company": "Unknown", "position": "Distributed Systems Engineering - Intern", "status": "Applied"}}

        2. *Email:* "We received an overwhelming response to the Software Developer, Intern position."  
           *Output:* {{"company": "BambooHR", "position": "Software Developer, Intern", "status": "Rejected"}}

        3. *Email:* "We are excited to invite you to an interview for the Machine Learning Intern role at Meta."  
           *Output:* {{"company": "Meta", "position": "Machine Learning Intern", "status": "Interview"}}

        4. *Email:* "Congratulations! We are pleased to offer you the Backend Developer position at Amazon!"  
           *Output:* {{"company": "Amazon", "position": "Backend Developer", "status": "Offer"}}

        *Return the extracted information in JSON format.*

        *Email Content:* {email_body}
        """
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    extracted_details = response.choices[0].message.content.strip()
    return extracted_details


def classify_email(subject: str):
    """Use GPT-4 to classify job-related emails"""
    prompt = f"""
        Classify whether the following email subject is related to a job application update or not. 

        *A job application update email means:*
        - Confirmation of application submission
        - Interview invitations or scheduling updates
        - Offer letters or rejection emails
        - Follow-up emails from recruiters regarding a specific application

        *DO NOT classify as a job application update if:*
        - The email is a job recommendation from LinkedIn, Indeed, or similar platforms
        - The email is about general career advice or networking
        - The email is a connection request or referral

        *Examples of job application update emails:*
        1. "Your application for Software Engineer at Google has been received."
        2. "Congratulations! You've been shortlisted for the next round at Amazon."
        3. "Rejection notice for your application to Facebook."

        *Examples of non-job application emails:*
        1. "Jobs you may be interested in from LinkedIn"
        2. "John Doe has invited you to connect on LinkedIn"
        3. "New job openings at Microsoft – Apply now!"

        Respond only with *'job'* if the email is a job application update or *'not job'* if it's not.

        Email Subject: {subject}
        """
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    classification = response.choices[0].message.content.strip().lower()
    return classification

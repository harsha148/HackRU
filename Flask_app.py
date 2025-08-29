import nltk
import pandas as pd
import PyPDF2
import pymupdf
import spacy
import os
from re import sub
import re
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from flask import Flask, render_template, request

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from transformers import pipeline
import requests

# generator = pipeline("text2text-generation", model="google/flan-t5-large")

nltk.download('punkt_tab')
nltk.download('stopwords')
spacy.cli.download("en_core_web_sm")
# Load SpaCy for Lemmatization
nlp = spacy.load("en_core_web_sm")

import openai
from openai import OpenAI
# OpenAI API Key (replace with your own API key)
openai.api_key = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# List of common technical skills and technologies (can be expanded as needed)
TECHNICAL_SKILLS = {
    "python", "java", "c++", "javascript", "react", "angular", "node.js", "docker",
    "kubernetes", "sql", "aws", "azure", "linux", "tensorflow", "pytorch",
    "machine learning", "deep learning", "nlp", "data", "science", "flask", "django", "excel", "r", "tableau",
    "time series", "word"
}


app = Flask(__name__)

# Set the folder for file uploads
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create the upload folder if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

import json

@app.route('/', methods=['GET', 'POST'])
def index():
    access_token = request.args.get('userId')
    messages = []
    df= None
    # filtered_df = None
    companies = None
    role_titles = None
    statuses = None
    learning_suggestions = None
    if access_token:
        # Use the access token to fetch Gmail messages from FastAPI
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get(f'http://localhost:8000/read_gmail?userId={access_token}')
        json_data = response.text
        response = json.loads(json_data)

        # Convert to DataFrame
        df = pd.DataFrame(response)

        # Extract the Object ID
        df["_id"] = df["_id"].apply(lambda x: x["$oid"])

        # Convert last_update date field to datetime format
        df["last_update"] = pd.to_datetime(df["last_update"].apply(lambda x: x["$date"]))
        # if response.status_code != 200:
        #     messages = ["Failed to fetch Gmail messages"]

        selected_role = request.form.get('role_title')
        # selected_location = request.form.get('location')
        selected_status = request.form.get('status')

        filtered_df = pd.DataFrame(response)
        print(filtered_df)
        if selected_role:
            filtered_df = filtered_df[filtered_df['position'] == selected_role]
        # if selected_location:
        #     filtered_df = filtered_df[filtered_df['Location'] == selected_location]
        if selected_status:
            filtered_df = filtered_df[filtered_df['status'] == selected_status]

        table_html = filtered_df.to_html(classes='table table-striped', index=False)
        companies = pd.DataFrame(response)['company'].unique().tolist()
        role_titles = pd.DataFrame(response)['position'].unique().tolist()
        # locations = pd.DataFrame(response)['Location'].unique()
        statuses = pd.DataFrame(response)['status'].unique().tolist()

        missing_skills = None
        # learning_suggestions = ""
        matching_score = None
        if 'resume' in request.files and 'job_description' in request.files:

        # if request.method == 'POST':
            resume_file = request.files['resume']
            job_description_file = request.files['job_description']

            if resume_file.filename != '' and job_description_file.filename != '':
                resume_filepath = os.path.join(app.config['UPLOAD_FOLDER'], resume_file.filename)
                job_desc_filepath = os.path.join(app.config['UPLOAD_FOLDER'], job_description_file.filename)

                resume_file.save(resume_filepath)
                job_description_file.save(job_desc_filepath)

                resume_text = extract_text_from_pdf(resume_filepath)
                job_desc_text = extract_text_from_pdf(job_desc_filepath)

                resume_text = preprocess_text(resume_text)
                job_desc_text = preprocess_text(job_desc_text)

                job_skills = extract_technical_skills(job_desc_text)
                resume_skills = extract_technical_skills(resume_text)

                missing_skills = find_missing_skills(job_skills, resume_skills)
                matching_score = calculate_matching_score(job_desc_text, resume_text)
                if missing_skills:
                    learning_suggestions = get_learning_suggestions(resume_skills, missing_skills)

                # Extract full sentences using regex
                # learning_suggestions = re.findall(r"\d+\.\s*(.*)",learning_suggestions)  # Extracts text after "1. ", "2. ", etc.

                learning_suggestions = [line.strip() for line in learning_suggestions.splitlines() if line.strip()]

                # If no numbering, fallback to normal split
                if not learning_suggestions:
                    learning_suggestions = learning_suggestions.split("\n")  # Split at sentence boundaries


            return render_template('index.html', table=table_html, role_titles=role_titles,
                               statuses=statuses, missing_skills=missing_skills, learning_suggestions=learning_suggestions,
                               matching_score=matching_score, df=df,messages=messages, access_token=access_token, companies=companies)
    return render_template('index.html',messages=messages, access_token=access_token, df= df,
                           companies = companies, role_titles = role_titles, statuses = statuses, learning_suggestions = learning_suggestions)

@app.route('/callback')
def callback():
    user_id = request.args.get('user_id')
    return f"Callback received for user {user_id}!"



def extract_text_from_pdf(filepath):
    with open(filepath, 'rb') as file:
        text = ""
        doc = pymupdf.open(file)

        for page in doc:
            text += page.get_text("text") + " "  # Extract text in reading order

    return text.strip()

# Preprocess text by removing punctuation, stopwords, and performing lemmatization
def preprocess_text(text):
    # Remove non-alphabetic characters (e.g., numbers, punctuation)
    text = sub(r'[^A-Za-z\s]', ' ', text)

    # Tokenize and remove stop words using nltk
    words = nltk.word_tokenize(text.lower())
    stopwords = nltk.corpus.stopwords.words('english')

    words = [word for word in words if word not in stopwords or word in TECHNICAL_SKILLS]

    return " ".join(words)


def extract_technical_skills(text):
    """
            Extract technical skills from a given document using Named Entity Recognition (NER)
            and keyword matching.
            """
    text = text.lower()  # Convert to lowercase for better matching
    doc = nlp(text)  # Process text using SpaCy

    extracted_skills = set()

    # Keyword matching from predefined skill list
    for token in doc:
        if token.text in TECHNICAL_SKILLS:
            extracted_skills.add(token.text)  # Preserve original skill name

    return list(extracted_skills)


def find_missing_skills(job_skills, resume_skills):

    # Identify missing skills (skills in job description but not in resume)
    missing_skills = [s for s in job_skills if s not in resume_skills]

    if missing_skills:
        return f"Missing Key Skills (as per the Job Description): " + "".join(
            [f"{skill}" for skill in missing_skills])
    else:
        return "<p>No missing skills. Your resume seems to match the job description!</p>"

model = SentenceTransformer('all-mpnet-base-v2')

def calculate_matching_score(job_desc_text, resume_text):

    # Convert both texts to embeddings using Sentence-BERT
    embeddings1 = model.encode([job_desc_text])
    embeddings2 = model.encode([resume_text])

    # Compute cosine similarity between the two embeddings
    similarity = cosine_similarity(embeddings1, embeddings2)
    score = similarity[0][0] * 100
    return f"{score:.2f}%"

# Clean the text: remove unwanted characters, trim spaces, and format
def clean_generated_text(text):
    # Remove extra spaces
    text = re.sub(r'\s+', ' ', text)

    # Remove unwanted leading/trailing spaces
    text = text.strip()

    # Optionally, remove unwanted characters like newline, tab, etc.
    text = re.sub(r'[\n\t]', ' ', text)

    # Further cleaning (if needed)
    text = text.replace(' .', '.').replace(' ,', ',')  # Fix punctuation spacing

    return text

def get_learning_suggestions(resume_skills, missing_skills):
    # Construct a detailed prompt to summarize missing skills and provide improvement suggestions
    prompt = f"""
        I have the following technical skills:
        {resume_skills}

        However, I am missing the following key skills:
        {', '.join(missing_skills)}

        How I can improve these skills
        1. What online resources I can refer to?
        2. What projects I can work on to gain hands-on experience?
        
        Answer these in detail and not as a paragraph summary
        """

    # Generate a response based on the prompt
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
                  {
                      "role": "user",
                      "content": prompt
                  }]
    )

    # suggestions = response[0]['generated_text']
    suggestions = response.choices[0].message

    suggestions = clean_generated_text(suggestions.content)

    return f"Learning Suggestions:{suggestions}"


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)

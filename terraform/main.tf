terraform {
  required_providers {
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.9.0"
    }
  }
}

provider "mongodbatlas" {
  public_key  = var.mongodb_public_key
  private_key = var.mongodb_private_key
}

resource "mongodbatlas_project" "icims_hackru" {
  name   = "icims"
  org_id = "6671dd8e012e6a43027b53db"
}

resource "mongodbatlas_cluster" "tf_managed_cluster" {
  project_id                  = mongodbatlas_project.icims_hackru.id
  name                        = "tf-managed-cluster"
  provider_name               = "AWS"
  provider_region_name        = "US_EAST_1"
  provider_instance_size_name = "M10"
}
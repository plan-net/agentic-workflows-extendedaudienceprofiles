"""
Script to upload prompts to Langfuse
"""
from .langfuse_prompt_manager import upload_current_prompts
import os
from dotenv import load_dotenv

load_dotenv()

def main():
    print("Uploading Extended Audience Profiles prompts to Langfuse...")
    PROMPT_LABEL = os.getenv("LANGFUSE_ENV", "production")
    versions = upload_current_prompts(PROMPT_LABEL)
    print(f"Successfully uploaded: {versions}")

if __name__ == "__main__":
    main()

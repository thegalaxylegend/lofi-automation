import os
from groq import Groq
from dotenv import load_dotenv
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

dotenv_path = r"c:\Users\Admin\OneDrive\Desktop\lofi-automation\.env"
load_dotenv(dotenv_path)

def list_groq_models():
    api_key = os.getenv("GROQ_API_KEY_1")
    if not api_key:
        print("ERROR: GROQ_API_KEY_1 not found in .env")
        return

    client = Groq(api_key=api_key)
    try:
        models = client.models.list()
        print("Available Groq Models:")
        for model in models.data:
            print(f"- {model.id}")
    except Exception as e:
        print(f"FAILED to list models: {e}")

if __name__ == "__main__":
    list_groq_models()

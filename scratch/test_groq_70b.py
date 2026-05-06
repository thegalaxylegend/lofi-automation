import os
from groq import Groq
from dotenv import load_dotenv
import sys

# Ensure UTF-8 output even on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Path to the .env file
dotenv_path = r"c:\Users\Admin\OneDrive\Desktop\lofi-automation\.env"
load_dotenv(dotenv_path)

def test_groq_70b():
    api_key = os.getenv("GROQ_API_KEY_1")
    if not api_key:
        print("ERROR: GROQ_API_KEY_1 not found in .env")
        return

    print(f"Testing Groq 70b with key: {api_key[:10]}...")
    
    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": "Hello! Can you confirm you are the llama-3.3-70b model? Please reply in one sentence."}],
            temperature=0.7,
            max_tokens=100,
        )
        print("SUCCESS! Reply from 70b:")
        print(response.choices[0].message.content)
    except Exception as e:
        print(f"FAILED! Error type: {type(e).__name__}")
        print(f"Error details: {str(e)}")

if __name__ == "__main__":
    test_groq_70b()

import os
from groq import Groq
from dotenv import load_dotenv
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

dotenv_path = r"c:\Users\Admin\OneDrive\Desktop\lofi-automation\.env"
load_dotenv(dotenv_path)

def test_groq_8b():
    api_key = os.getenv("GROQ_API_KEY_1")
    if not api_key:
        print("ERROR: GROQ_API_KEY_1 not found in .env")
        return

    client = Groq(api_key=api_key)
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": "Reply with 'OK' if you can hear me."}],
            temperature=0.7,
            max_tokens=10,
        )
        print(f"SUCCESS! Reply: {response.choices[0].message.content}")
    except Exception as e:
        print(f"FAILED! Error: {e}")

if __name__ == "__main__":
    test_groq_8b()

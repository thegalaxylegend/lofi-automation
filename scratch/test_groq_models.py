import os
from groq import Groq
from dotenv import load_dotenv
import sys

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

dotenv_path = r"c:\Users\Admin\OneDrive\Desktop\lofi-automation\.env"
load_dotenv(dotenv_path)

def test_groq_models():
    api_key = os.getenv("GROQ_API_KEY_1")
    if not api_key:
        print("ERROR: GROQ_API_KEY_1 not found in .env")
        return

    models = ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "llama3-70b-8192", "mixtral-8x7b-32768"]
    client = Groq(api_key=api_key)

    for model in models:
        print(f"\n--- Testing model: {model} ---")
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with 'OK' if you can hear me."}],
                temperature=0.7,
                max_tokens=10,
            )
            print(f"✅ SUCCESS! Reply: {response.choices[0].message.content}")
        except Exception as e:
            print(f"❌ FAILED! Error: {str(e)[:100]}...")

if __name__ == "__main__":
    test_groq_models()

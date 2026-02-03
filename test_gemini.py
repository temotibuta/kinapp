
import os
import google.generativeai as genai
from dotenv import load_dotenv

# Force reload of environment variables
load_dotenv(override=True)

api_key = os.environ.get("GEMINI_API_KEY")
print(f"Loaded API Key: {api_key}") 

if api_key:
    genai.configure(api_key=api_key)
    try:
        # Try the model we want to use
        model_name = 'gemini-flash-latest'
        print(f"Testing generation with {model_name}...")
        model = genai.GenerativeModel(model_name)
        response = model.generate_content("Hello")
        print("Success! Response:", response.text)
    except Exception as e:
        print("Error during generation:", e)
else:
    print("No API Key found in env")

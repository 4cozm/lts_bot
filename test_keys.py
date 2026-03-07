import os
from dotenv import load_dotenv
load_dotenv()
print("KEY:", bool(os.environ.get("GEMINI_API_KEY")))

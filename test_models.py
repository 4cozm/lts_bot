from google import genai

client = genai.Client()
for m in client.models.list():
    if getattr(m, 'supported_generation_methods', None) and 'bidiGenerateContent' in m.supported_generation_methods:
        print(m.name)

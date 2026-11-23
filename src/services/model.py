# here we will use a model service and pass the context and user input to the model and get the response

# lets use google gemini api for now
from google import genai

# ---- SYSTEM PROMPT ----
# Sets the LLM's persona, behavior rules, and constraints
SYSTEM_PROMPT = """You are a medical information assistant specialized in sinusitis and sinus-related conditions.

Your behavior rules:
- Answer ONLY using the provided context below. Do not use outside knowledge.
- If the answer is not found in the context, say: "I don't have enough information in the provided documents to answer that."
- Be concise and clear. Avoid unnecessary medical jargon.
- Never diagnose or prescribe. Always recommend consulting a doctor for personal medical decisions.
- If the context contains conflicting information, acknowledge it.
"""

class ModelService:
    def __init__(self):
        # NOTE: the Gemini API key used to be hardcoded here and has been removed before publishing.
        # Set it as an environment variable instead: export GEMINI_API_KEY="your-key"
        # genai.Client() picks up GEMINI_API_KEY automatically.
        self.client = genai.Client()

    def get_response_from_LLM(self, context: str, user_input: str):
        response = self.client.models.generate_content_stream(
            model="gemini-3-flash-preview",
            contents=[f"Context: {context}\nUser input: {user_input}"],
            config={
                "temperature": 1.0,
                "system_instruction": SYSTEM_PROMPT
            }
        )
        return response
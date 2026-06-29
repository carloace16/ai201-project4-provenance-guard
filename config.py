import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
LLM_MODEL = "llama-3.3-70b-versatile"

# Confidence thresholds
HIGH_AI_THRESHOLD = 0.7
HIGH_HUMAN_THRESHOLD = 0.4

# Signal weights
LLM_WEIGHT = 0.6
STYLO_WEIGHT = 0.4

# SQLite audit log
DB_PATH = "audit.db"
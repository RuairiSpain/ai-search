"""Exercise 10 - Replace deterministic vectors with real Azure OpenAI embeddings.

Requires .env values:
AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_EMBEDDING_DEPLOYMENT
Run: python exercises/10_real_embeddings_start.py
"""
from pathlib import Path
import os
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from openai import AzureOpenAI
from src.providers import AzureSearchProvider
from src.data_loader import load_sample_documents

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
)
deployment = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]
provider = AzureSearchProvider()


def embed(text: str) -> list[float]:
    # TODO 1: call Azure OpenAI embeddings for the supplied text.
    response = None  # SOLUTION: response = client.embeddings.create(model=deployment, input=text)
    if response is None:
        raise SystemExit("Exercise incomplete: fill TODO 1 or uncomment the SOLUTION comment.")
    return response.data[0].embedding


documents = load_sample_documents(with_vectors=False)
for doc in documents:
    # TODO 2: populate both vector fields using the real embedding model.
    doc["titleVector"] = None  # SOLUTION: doc["titleVector"] = embed(doc["title"])
    doc["contentVector"] = None  # SOLUTION: doc["contentVector"] = embed(doc["content"])
    if doc["titleVector"] is None or doc["contentVector"] is None:
        raise SystemExit("Exercise incomplete: fill TODO 2 or uncomment the SOLUTION comments.")

results = provider.upload_documents(documents)
for r in results:
    print(r["key"], r["succeeded"])

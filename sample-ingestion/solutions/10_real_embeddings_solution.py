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
    response = client.embeddings.create(model=deployment, input=text)
    return response.data[0].embedding


documents = load_sample_documents(with_vectors=False)
for doc in documents:
    doc["titleVector"] = embed(doc["title"])
    doc["contentVector"] = embed(doc["content"])

results = provider.upload_documents(documents)
for r in results:
    print(r["key"], r["succeeded"])

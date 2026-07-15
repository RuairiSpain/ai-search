from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import LocalSearchProvider, load_sample_index_definition
from src.data_loader import load_sample_documents

provider = LocalSearchProvider()
provider.create_or_update_index(load_sample_index_definition())
documents = load_sample_documents(with_vectors=True)
results = provider.upload_documents(documents)
print(f"Seeded {len(results)} local documents")

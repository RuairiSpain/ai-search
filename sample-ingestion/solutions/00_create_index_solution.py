from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))
from src.providers import AzureSearchProvider, load_sample_index_definition

provider = AzureSearchProvider()
result = provider.create_or_update_index(load_sample_index_definition())
print(f"Created or updated index: {result['name']}")

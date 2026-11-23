# from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
# import boto3

# region = "us-east-2"
# host = "g38d5ebxn5c8w9ap2ooi.us-east-2.aoss.amazonaws.com"  # no https:// prefix
# index_name = "sinus_chunks"

# # Use the IAM user/profile you created
# session = boto3.Session(profile_name="opensearch-dev", region_name=region)
# credentials = session.get_credentials()

# auth = AWSV4SignerAuth(credentials, region, "aoss")

# client = OpenSearch(
#     hosts=[{"host": host, "port": 443}],
#     http_auth=auth,
#     use_ssl=True,
#     verify_certs=True,
#     connection_class=RequestsHttpConnection,
#     timeout=30,
# )

# mapping = {
#     "settings": {
#         "index": {
#             "knn": True
#         }
#     },
#     "mappings": {
#         "properties": {
#             "chunk_id": {"type": "keyword"},
#             "chunk_index": {"type": "integer"},
#             "source_file": {"type": "keyword"},
#             "source_type": {"type": "keyword"},
#             "source_path": {"type": "keyword"},
#             "text": {"type": "text"},
#             "char_count": {"type": "integer"},
#             "embedding": {
#                 "type": "knn_vector",
#                 "dimension": 1024  # must match your Bedrock embedding size
#             }
#         }
#     }
# }

# # if not client.indices.exists(index=index_name):
# #     resp = client.indices.create(index=index_name, body=mapping)
# #     print("Index created:", resp)
# # else:
# #     print("Index already exists")

# def store_embedding(
#     embedding: list, 
#     chunk_id: str, 
#     chunk_index: int, 
#     source_file: str, 
#     source_path: str, 
#     source_type: str, 
#     char_count: int,
#     text: str
# ):
#     resp = client.index(index='sinus_chunks', body={
#         "embedding": embedding,
#         "chunk_id": chunk_id,
#         "chunk_index": chunk_index,
#         "source_file": source_file,
#         "source_path": source_path,
#         "source_type": source_type,
#         "char_count": char_count,
#         "text": text
#     })
#     print(resp)
#     return resp

# def search_by_id(chunk_id: str):
#     resp = client.search(index='sinus_chunks', body={
#         "size": 1,
#         "query": {
#             "match": {
#                 "chunk_id": chunk_id
#             }
#         }
#     })
#     print(resp)
#     return resp

# def search_embeddings(
#     embedding: list,
#     k: int = 5
# ):
#     resp = client.search(index='sinus_chunks', body={
#         "size": k,
#         "query": {
#             "knn": {
#                 "field": "embedding",
#                 "vector": embedding
#             }
#         },
#         "sort": [
#             {"_score": "desc"}
#         ]
#     })
#     print(resp)
#     return resp

# lets use chroma for storing the embeddings
import chromadb

client = chromadb.PersistentClient(path="../data/db/chroma_db")
collection = client.get_or_create_collection("sinus_chunks")

def store_embedding(embedding_dict: dict):
    embedding = embedding_dict['embedding']
    chunk_id = embedding_dict['chunk_id']
    chunk_index = embedding_dict['chunk_index']
    source_file = embedding_dict['source_file']
    source_path = embedding_dict['source_path']
    source_type = embedding_dict['source_type']
    char_count = embedding_dict['char_count']
    text = embedding_dict['text']
    
    collection.add(
        embeddings=[embedding],
        documents=[text],
        metadatas=[{"chunk_index": chunk_index, 
            "source_file": source_file, 
            "source_path": source_path, 
            "source_type": source_type, 
            "char_count": char_count,
            "chunk_id": chunk_id
        }],
        ids=[chunk_id]
    )

def search_embeddings(embedding: list, k: int = 5):
    results = collection.query(
        query_embeddings=[embedding],
        n_results=k
    )
    return results

def peek_vector_store():
    client = chromadb.PersistentClient(path="../data/db/chroma_db")
    collection = client.get_or_create_collection("sinus_chunks")
    print(collection.peek())
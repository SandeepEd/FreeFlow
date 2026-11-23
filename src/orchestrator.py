import sys, os
sys.path.append('..')
from src.chunker import Chunker
from src.loader import Loader
from pathlib import Path
import json
from typing import Iterable
from src.services.embedding import generate_embedding
from src.services.vector_store import store_embedding, peek_vector_store, search_embeddings
from src.services.model import ModelService


class Orchestrator:
    def __init__(self, raw_data_path: str):
        self.raw_data_path = raw_data_path
        self.chunks_data_path = os.path.join(self.raw_data_path, 'chunks')

    def orchestrate(self):
        html_data_path = os.path.join(self.raw_data_path, 'html')
        pdf_data_path = os.path.join(self.raw_data_path, 'pdfs')
        supported_files = ['html', 'pdf']
        html_files = self.get_file_from_directory(html_data_path, supported_files)
        pdf_files = self.get_file_from_directory(pdf_data_path, supported_files)
        
        for file in html_files:
            self.load_data_and_chunk(data_path=file)
        for file in pdf_files:
            self.load_data_and_chunk(data_path=file)
    
    def get_file_from_directory(self, directory: str, supported_files: list):
        files = []
        for file in os.listdir(directory):
            if file.endswith(tuple(supported_files)):
                files.append(os.path.join(directory, file))
        return files

    def load_data_and_chunk(self, data_path: str):
        loader = Loader(data_path)
        data = loader.load()
        chunker = Chunker(data, data_path=data_path)
        chunks = chunker.chunk()
        self.save_chunks(chunks, data_path=data_path)



    def save_chunks(self, chunks: Iterable[str], data_path: str) -> None:
        """
        Save chunks as JSONL (one JSON object per line).

        Why JSONL:
        - easy to stream/read later
        - easy to attach metadata
        - great for embedding/indexing pipelines
        """
        source_path = Path(data_path)
        source_file = source_path.name
        source_stem = source_path.stem

        output_dir = Path(self.raw_data_path) / "chunks"
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / f"{source_stem}.jsonl"

        with output_path.open("w", encoding="utf-8") as f:
            for chunk_index, chunk_text in enumerate(chunks):
                chunk_text = chunk_text.strip()
                if not chunk_text:
                    continue

                record = {
                    "chunk_id": f"{source_stem}_chunk_{chunk_index}",
                    "chunk_index": chunk_index,
                    "source_file": source_file,
                    "source_path": str(source_path),
                    "source_type": source_path.suffix.lower().lstrip("."),  # pdf / html
                    "text": chunk_text,
                    "char_count": len(chunk_text),
                }

                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def read_chunks_from_jsonl(self, data_path: str):
        with open(data_path, 'r', encoding='utf-8') as file:
            return [json.loads(line) for line in file]
    
    def get_file_name_from_path(self, data_path: str):
        return Path(data_path).stem

    def generate_embeddings(self, chunks: [str]):
        embeddings = []
        for chunk in chunks:
            model_id = "amazon.titan-embed-text-v2:0"
            body = json.dumps({
                "inputText": chunk['text'],
            })
            embedding = generate_embedding(model_id=model_id, body=body)
            # print(f"\x1b[32mGenerated embedding for chunk {chunk['chunk_id']} with length {len(embedding['embedding'])}\x1b[0m")
            # print(f"\x1b[32membedding: {embedding['embedding']}\x1b[0m")
            embeddings.append({
                "embedding": embedding['embedding'],
                "chunk_id": chunk['chunk_id'],
                "chunk_index": chunk['chunk_index'],
                "source_file": chunk['source_file'],
                "source_path": chunk['source_path'],
                "source_type": chunk['source_type'],
                "char_count": chunk['char_count'],
                "text": chunk['text']})
        return embeddings

    def generate_embeddings_and_store(self):
        files = self.get_file_from_directory(self.chunks_data_path, ['jsonl'])
        print(f"\x1b[32mFound {len(files)} files to embed and store\x1b[0m")
        print(f"\x1b[32mFiles: {files}\x1b[0m")
        for file_path in files:
            chunks = self.read_chunks_from_jsonl(file_path)
            embeddings = self.generate_embeddings(chunks=chunks)
            for embedding in embeddings:
                store_embedding(embedding_dict={
                        "embedding": embedding['embedding'], 
                        "chunk_id": embedding['chunk_id'], 
                        "chunk_index": embedding['chunk_index'], 
                        "source_file": embedding['source_file'], 
                        "source_path": embedding['source_path'], 
                        "source_type": embedding['source_type'], 
                        "char_count": embedding['char_count'], 
                        "text": embedding['text']
                    })


    def inspect_vector_store(self):
        peek_vector_store()

    def get_input_from_user(self):
        user_input = input("Please enter the input: ")
        return user_input
    
    def search_vector_store_for_user_input(self, user_input: str):
        embedding = generate_embedding(model_id="amazon.titan-embed-text-v2:0", body=json.dumps({"inputText": user_input}))
        results = search_embeddings(embedding=embedding['embedding'], k=5)
        # results['documents'][0] is a list of matched chunk texts — join them into a single context string
        context = "\n\n".join(results['documents'][0])
        return context

    # Lets stream the response from the LLM
    def get_response_from_LLM(self, context: str, user_input: str):
        # model_service must stay alive for the entire stream — keeping it in
        # this generator's frame prevents the GC from closing the httpx client early
        model_service = ModelService()
        for chunk in model_service.get_response_from_LLM(context=context, user_input=user_input):
            if chunk.text:
                yield chunk.text

if __name__ == "__main__":
    orchestrator = Orchestrator('../data/raw/')
    # orchestrator.orchestrate()
    # orchestrator.generate_embedding(file_name='medlineplus.gov_sinusitis_0db11d5a42815287')
    # orchestrator.generate_embeddings_and_store()
    # orchestrator.inspect_vector_store()

    # we need to get the input from the user through the terminal and then search the vector store for the most similar chunks
    user_input = orchestrator.get_input_from_user()
    # print(f"\x1b[32mUser input: {user_input}\x1b[0m")
    context = orchestrator.search_vector_store_for_user_input(user_input=user_input)
    response = orchestrator.get_response_from_LLM(context=context, user_input=user_input)
    for chunk in response:
        print(f"\x1b[33m{chunk}\x1b[0m", end="", flush=True)
    print()  # newline after stream ends
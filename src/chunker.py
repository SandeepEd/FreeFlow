from langchain_text_splitters import RecursiveCharacterTextSplitter
from trafilatura import extract

class Chunker:
    def __init__(self, data: str, data_path: str):
        self.data = data
        self.data_path = data_path

    def chunk(self):
        if '.pdf' in self.data_path:
            return self.chunk_text_data(self.data)
        elif '.html' in self.data_path:
            main_content = self.extract_main_content_from_html()
            return self.chunk_text_data(main_content)
        else:
            raise ValueError(f"Unsupported file type: {self.data_path}")


    def chunk_text_data(self, text: str):
        splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        return splitter.split_text(text)
    
    def chunk_html(self):
        main_content = self.extract_main_content_from_html()
        return self.chunk_text_data(main_content)
        # print(f"\x1b[33mMain content: {main_content}\x1b[0m")
        # headers_to_split_on = [
        #     ("h1", "H1"),
        #     ("h2", "H2"),
        #     ("h3", "H3"),
        # ]
        # html_splitter = HTMLSectionSplitter(headers_to_split_on=headers_to_split_on)
        # split_text = html_splitter.split_text(self.data)
        # print(f"\x1b[31mSplit text: {split_text}\x1b[0m")
        
        return split_text

    def extract_main_content_from_html(self):
        return extract(self.data, favor_precision=True)
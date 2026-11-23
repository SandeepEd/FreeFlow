class Loader:
    def __init__(self, data_path: str):
        self.data_path = data_path

    def load(self):
        # the file could be either pdf, html, or text
        if self.data_path.endswith('.pdf'):
            return self.load_pdf()
        elif self.data_path.endswith('.html'):
            return self.load_html()
        else:
            raise ValueError(f"Unsupported file type: {self.data_path}")

    def load_pdf(self):
        # lets use pypdf to load the pdf
        from pypdf import PdfReader
        with open(self.data_path, 'rb') as file:
            reader = PdfReader(file)
            return '\n'.join([page.extract_text() for page in reader.pages])

    def load_html(self):
        with open(self.data_path, 'r', encoding='utf-8') as file:
            return file.read()
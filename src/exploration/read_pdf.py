import sys
import subprocess
try:
    import pypdf
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pypdf'])
    import pypdf

def read_pdf(file_path):
    print(f'--- {file_path} ---')
    try:
        reader = pypdf.PdfReader(file_path)
        for i, page in enumerate(reader.pages):
            print(f'Page {i+1}:')
            print(page.extract_text())
    except Exception as e:
        print(f'Error reading {file_path}: {e}')

read_pdf('/Users/vishaljha/Desktop/Homeworks/MLOps/Project/Mlops project/MLOps_Spotiboys_Project_Proposal.pdf')
read_pdf('/Users/vishaljha/Desktop/Homeworks/MLOps/Project/Mlops project/MLOps_slides.pdf')

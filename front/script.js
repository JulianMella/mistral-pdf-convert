document.addEventListener('DOMContentLoaded', () => {

    const fileButton = document.getElementById('file-button');
    const fileInput = document.getElementById('file-input');
    const form = document.getElementById('upload-form');

    fileButton.addEventListener('click', () => fileInput.click());

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            fileButton.textContent = fileInput.files[0].name;
        } else {
            fileButton.textContent = 'Select PDF';
        }
    });


    form.addEventListener('submit', function(event) {
        event.preventDefault();

        const apiKey = form.apiKey.value;
        const pdfFile = document.getElementById('file-input').files[0];
        const formData = new FormData();
        formData.append('api_key', apiKey);
        formData.append('pdf_file', pdfFile);

        fetch('http://127.0.0.1:8001/api/ocr-pdf', {
            method: 'POST',
            body: formData,
        })
        .then(response => response.json()
        .then(result => {
            console.log(result);
            renderMarkdown(result.text)
        }))
        .catch(error => {
            console.error('Error:', error);
        });


    });
});

function renderMarkdown(text) {
    const container = document.getElementById('contents');
    container.style.color = 'white';
    container.innerHTML = marked.parse(text);
    const btn = document.createElement('button');
    btn.textContent = 'Download Markdown';
    btn.onclick = () => {
        const blob = new Blob([text], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'output.md';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };
    container.appendChild(btn);
}
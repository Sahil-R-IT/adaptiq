# 🚀 Adaptiq

> Turn any content — web pages, PDFs, YouTube videos, or documents — into Quiz using AI.

---

## 🧠 What is Adaptiq?

**Adaptiq** is an AI-powered Flask application that intelligently extracts, processes, and transforms content from multiple sources into usable knowledge.

Instead of manually reading, copying, and summarizing information, Adaptiq automates the entire pipeline:

* 🔗 Web pages → Extract → Clean → Summarize
* 📄 PDFs / Docs → Parse → Analyze → Structure
* 🎥 YouTube → Transcript → Insights
* 🧠 AI (Gemini) → Understand → Generate output

---

## ✨ Features

### 🌐 Multi-Source Input

* URLs (web scraping with BeautifulSoup)
* YouTube videos (transcript extraction)
* PDF files (text extraction via `pypdf`)
* DOCX files (`python-docx`)

### 🧠 AI-Powered Processing

* Uses **Google Gemini (google-genai)** for:

  * Content understanding
  * Summarization
  * Intelligent transformation

### ⚡ Clean Modular Architecture

* `services/` handles all extraction logic
* Separation of concerns → scalable and maintainable

### 🖥️ Simple UI

* Flask + HTML + JS frontend
* Fast interaction and processing

---

## 🏗️ Project Structure

```
adaptiq/
│
├── app.py                 # Main Flask application
├── requirements.txt
│
├── services/              # Core logic
│   ├── extract_files.py
│   ├── extract_web.py
│   ├── extract_youtube.py
│   └── source_detector.py
│
├── templates/
│   └── index.html
│
├── static/
│   ├── script.js
│   └── style.css
│
└── uploads/              # Runtime uploaded files
```

---

## ⚙️ Installation

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/adaptiq.git
cd adaptiq
```

### 2. Create virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 🔑 Environment Setup

Create a `.env` file in the root directory:

```env
GOOGLE_API_KEY=your_api_key_here
```

> ⚠️ Never commit your `.env` file

---

## ▶️ Run the App

```bash
python app.py
```

Then open:

```
http://127.0.0.1:5000
```

---

## 🔍 How It Works

1. User provides input (URL / file / YouTube link)
2. `source_detector.py` identifies content type
3. Corresponding service extracts raw data
4. Data is processed and sent to **Gemini AI**
5. Clean output is returned to the UI

---

## 🧪 Example Use Cases

* 📚 Summarize research articles
* 🎥 Extract insights from long YouTube videos
* 🌐 Convert blog posts into structured notes
* 📄 Analyze uploaded documents instantly

---

## 🧩 Tech Stack

* **Backend:** Flask
* **Frontend:** HTML, CSS, JavaScript
* **AI:** Google Gemini (`google-genai`)
* **Parsing:** BeautifulSoup, pypdf, python-docx

---

## ⚠️ Known Limitations

* Relies on transcript availability for YouTube
* Web scraping may fail on heavily protected sites
* AI output depends on prompt quality

---

## 🔮 Future Improvements

* User authentication system
* History tracking
* Export results (PDF/Markdown)
* Advanced prompt customization
* Async processing for performance

---

## 🤝 Contributing

Pull requests are welcome. If you want to improve the project:

1. Fork the repo
2. Create a new branch
3. Submit a PR

---

## 📜 License

MIT License

---

## 💡 Final Thought

Most tools help you *consume* content.

**Adaptiq helps you *understand* it.**

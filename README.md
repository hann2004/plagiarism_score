# 🛡️ Integrity Observatory

**Integrity Observatory** is an enterprise-grade academic integrity and plagiarism detection platform tailored for cohort-based learning programs and autograders. Powered by **JPlag Abstract Syntax Tree (AST)** token analysis and text-matching engines, it delivers real-time cluster visualization, seriation heatmaps, automated file conversions, and side-by-side match inspection across code and written report submissions.

---

## 🌟 Key Features

### 1. 🧬 Multi-Language AST & Text Similarity Engine
- **Structure-Aware Code Analysis**: Uses JPlag's Abstract Syntax Tree tokenisation to detect structural code plagiarisms, ignoring variable renaming, comment alterations, or reordered blocks.
- **Language Coverage**: Supports Python, Java, C, C++, C#, JavaScript, TypeScript, Go, Rust, SQL, HTML, CSS, and Plain Text reports.
- **Automatic Language Detection**: Scans cohort repositories to detect the dominant programming language automatically.

### 2. 📄 Automated Document & Notebook Converters
- **Jupyter Notebooks (`.ipynb` $\rightarrow$ `.py`)**: Extracts Python code cells from Data Science & ML notebook submissions automatically.
- **Word Documents (`.docx` $\rightarrow$ `.txt`)**: Converts Microsoft Word files into clean plain text for report-mode analysis.
- **PDF Reports (`.pdf` $\rightarrow$ `.txt`)**: Extracts textual content from PDF submissions for similarity checking.
- **Sanitization Pipeline**: Automatically filters out licenses, boilerplate headers, `README.md` documentation, and non-target binary files before running comparison engines.

### 3. ⚡ CSV Batch Processing & Parallel Fetcher
- **10 Academy CSV Support**: Directly ingests cohort CSV exports, parsing student GitHub repository links and Google Docs/Drive URLs.
- **Concurrent Repository Cloning**: Clones GitHub repositories and downloads Google Docs in parallel using high-concurrency thread pools (`ThreadPoolExecutor`).
- **Skipped Student Auditing**: Tracks and reports student folders that contained no valid source files after sanitization, with instant UI inspection modals.

### 4. 📊 Rich Interactive Visualizations & Dashboards
- **Side-by-Side Diff Viewer**: Dual-pane code view with synchronized scrolling, exact match line highlighting, and match navigation controls (`◀ Match 1 / N ▶`).
- **3D Cluster Network Graph**: Interactive force-directed network graph clustering plagiarized student groups by threshold.
- **Seriation Heatmap**: Ordered similarity matrix highlighting plagiarized clusters along the diagonal.
- **Cohort Health KPIs**: Real-time stats on total students, analyzed pairs, flagged pairs ($\ge 80\%$), peak similarity, and overall coverage percentage.

---

## 📁 Project Architecture

```
.
├── backend/
│   ├── main.py              # FastAPI application & REST routing
│   ├── jplag_runner.py       # JPlag execution, multi-version JSON parser & fallbacks
│   ├── csv_processor.py     # 10 Academy CSV parser & parallel git/doc fetcher
│   ├── fetcher.py           # GitHub repo cloner & Google Docs downloader
│   ├── ipynb_converter.py   # Jupyter Notebook (.ipynb -> .py) code cell extractor
│   ├── docx_converter.py    # Word (.docx -> .txt) text converter
│   ├── pdf_converter.py     # PDF (.pdf -> .txt) text converter
│   ├── language_detector.py # Automatic codebase language detection
│   ├── storage.py           # JSON batch data storage engine & cohort indexing
│   ├── requirements.txt     # Python backend dependencies
│   └── jplag/
│       └── jplag.jar        # JPlag engine executable JAR
├── frontend/
│   ├── index.html           # Single-page web dashboard (HTML5, Vanilla CSS, JS)
│   ├── submit.html          # Student link submission portal
│   ├── d3.min.js            # D3.js visualization library
│   ├── three.module.min.js  # Three.js 3D rendering library
│   └── OrbitControls.js     # 3D camera navigation controls
├── Dockerfile               # Production multi-stage Docker configuration
├── .dockerignore
└── README.md                # Platform documentation
```

---

## 🚀 Quick Start (Local Setup)

### System Requirements
- **Java JRE/JDK 17+**: Verify with `java -version`
- **Python 3.10+**: Verify with `python3 --version`
- **Git**: Required for GitHub repository cloning

### Installation Steps

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/hann2004/plagiarism_score.git
   cd plagiarism_score
   ```

2. **Create & Activate Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate   # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

4. **Verify JPlag Executable**:
   Ensure `backend/jplag/jplag.jar` is present. If missing, download it from [JPlag Releases](https://github.com/jplag/JPlag/releases).

5. **Launch the Server**:
   ```bash
   cd backend
   uvicorn main:app --reload --port 8000
   ```

6. **Access Dashboard**:
   Open `http://localhost:8000` in your web browser.

---

## 📡 API Endpoint Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/cohorts` | List all active cohorts, weekly run metrics, and flagged pairs count |
| `GET` | `/api/cohorts/{cohort_id}/batches/{batch_id}` | Retrieve lightweight comparison pairs and batch summary stats |
| `GET` | `/api/cohorts/{cohort_id}/batches/{batch_id}/submissions` | List registered student submission URLs for a batch |
| `POST` | `/api/cohorts/{cohort_id}/batches/{batch_id}/submissions` | Register a student's GitHub/Google Doc link |
| `GET` | `/api/cohorts/{cohort_id}/batches/{batch_id}/pairs/{pair_id}/files` | Fetch side-by-side source code and line match ranges for a pair |
| `POST` | `/api/run` | Process and run JPlag on an uploaded `.zip` of student folders |
| `POST` | `/api/run-fetched` | Automatically fetch stored GitHub/Doc links and run comparison |
| `POST` | `/api/run-csv` | Upload a 10 Academy CSV file to fetch repos/docs and execute batch run |
| `POST` | `/api/cohorts/{cohort_id}/batches/{batch_id}/status` | Update pair investigation status (`Pending`, `Confirmed`, `Cleared`) |
| `POST` | `/api/clean-empty` | Clean empty unanalyzed batches and empty cohorts |
| `DELETE` | `/api/cohorts/{cohort_id}/batches/{batch_id}` | Purge a batch run and remove stored student source files |

---

## 🐳 Containerization & Cloud Deployment

### Running with Docker

```bash
# Build the Docker image
docker build -t integrity-observatory .

# Run container exposing port 8000
docker run -d -p 8000:8000 -e PORT=8000 --name integrity_app integrity-observatory
```

### Deploying to Render / Cloud Hosting
- **Runtime**: Docker or Python Web Service
- **Build Command**: `pip install -r backend/requirements.txt`
- **Start Command**: `uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- **Memory Optimization**: The backend automatically caps Java heap memory (`-Xmx512m`) to run reliably on free/starter container tiers without hitting OOM limits.

---

## 📜 License & Credits

Built for academic integrity evaluation across cohorts. Powered by the open-source [JPlag AST Engine](https://github.com/jplag/JPlag).

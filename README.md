# 🛡️ Integrity Observatory

**Integrity Observatory** is an enterprise-grade academic integrity and plagiarism detection dashboard designed for cohort-wide submission analysis. Built on top of **JPlag AST (Abstract Syntax Tree)** token parsing, it provides interactive visual cluster graphs, seriation heatmaps, and side-by-side code diffs to investigate code and document similarity across student cohorts.

---

## 🌟 Key Features

### 1. 🧬 AST-Based Code & Report Analysis
- **Language-Aware Parsing**: Uses JPlag's Abstract Syntax Tree engine to detect structural code similarities regardless of renamed variables, reordered functions, or modified comments.
- **Multi-Language Support**: Supports Python 3, Java, C, C++, C#, JavaScript, TypeScript, and Plain Text reports.
- **Automatic Language Detection**: Automatically scans submissions and identifies the dominant language across student repositories.

### 2. ⚡ Automatic File Converters
- **Jupyter Notebook (`.ipynb` $\rightarrow$ `.py`)**: Automatically parses Jupyter Notebooks, extracts Python code cells, and generates clean `.py` files for JPlag analysis. Essential for Data Science & ML cohorts.
- **Word Document (`.docx` $\rightarrow$ `.txt`)**: Recursively converts Microsoft Word documents and tables into `.txt` format for text-mode comparison.

### 3. 📊 Modern Visualizations & Dashboards
- **Seriation Matrix Heatmap**: Nearest-neighbor ordered similarity matrix that clusters plagiarized student groups along the diagonal with custom color scales and hover glows.
- **Small Multiples Cluster Graph**: Interactive force-directed network graphs that group student clusters based on matching thresholds with convex hulls.
- **4 KPI Summary Metrics**: Real-time stats showing total students compared, flagged pairs ($\ge 90\%$), highest similarity peak, and cohort average similarity.

### 4. 🔍 Side-by-Side Diff Viewer
- **Dual-Pane Code Inspection**: Compare Student A and Student B files side-by-side with synchronized scrolling.
- **Match Highlighting**: Highlights exact matching token fragments in vivid red (active) and amber (secondary).
- **Match Navigation Bar**: Jump directly between matches (`◀ Match 1 / 3 ▶`) with smooth auto-scrolling.
- **Multi-File Selector**: Switch between individual files or view combined submissions.

### 5. 🔄 Automated Fetching & 10 Academy Integration
- **GitHub & Google Docs Fetching**: Automatically clone GitHub repos (`git clone --depth 1`) or download Google Docs/Drive files for an entire batch.
- **10 Academy Autograder Integration**: Ready-to-connect endpoints and background scheduler (`APScheduler`) to run automatic checks on submission deadlines.
- **Status Workflow**: Mark review status for every pair (`Pending`, `Confirmed`, `Cleared`).

---

## 📁 Project Structure

```
.
├── backend/
│   ├── main.py              # FastAPI server, routing, static file hosting
│   ├── jplag_runner.py       # JPlag CLI execution & topComparisons JSON parser
│   ├── fetcher.py           # GitHub repo cloning & Google Docs exporter
│   ├── ipynb_converter.py   # Jupyter Notebook (.ipynb -> .py) code cell extractor
│   ├── docx_converter.py    # Word (.docx -> .txt) document converter
│   ├── language_detector.py # Automatic file extension scanner
│   ├── storage.py           # JSON-file storage per batch + index directory
│   ├── requirements.txt     # Python dependencies
│   ├── jplag/
│   │   └── jplag.jar        # JPlag executable JAR (v5.x+)
│   ├── data/                # Persistent JSON batch data & raw student files
│   └── work/                # Temporary processing directory for active runs
├── frontend/
│   ├── index.html           # Single-page web app (HTML5, Vanilla CSS, JS)
│   └── d3.min.js            # D3.js v7 library for network graphs & heatmaps
├── Dockerfile               # Production Docker deployment setup
├── .dockerignore
└── README.md                # Project documentation
```

---

## 🚀 Quick Start (Local Setup)

### Prerequisites
1. **Java JDK (17+)**: Verify installation with `java -version`.
2. **Python (3.9+)**: Verify installation with `python3 --version`.

### Installation Steps

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/hann2004/plagiarism_score.git
   cd plagiarism_score
   ```

2. **Set Up Python Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r backend/requirements.txt
   ```

4. **Verify JPlag Executable**:
   Ensure `jplag.jar` is present at `backend/jplag/jplag.jar`. Download the release from [JPlag Releases](https://github.com/jplag/JPlag/releases) if needed.

5. **Start the Application**:
   ```bash
   cd backend
   uvicorn main:app --reload --port 8000
   ```

6. **Open in Browser**:
   Navigate to `http://localhost:8000` to access the live dashboard.

---

## 📡 API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `GET /api/cohorts` | `GET` | List all cohorts and their weekly run statistics |
| `GET /api/cohorts/{cohort_id}/batches/{batch_id}` | `GET` | Get full comparison results for a specific batch |
| `GET /api/cohorts/{cohort_id}/batches/{batch_id}/pairs/{pair_id}/files` | `GET` | Fetch side-by-side files & match ranges for a pair |
| `POST /api/run` | `POST` | Upload a ZIP of student folders and run JPlag |
| `POST /api/run-fetched` | `POST` | Automatically fetch stored GitHub/Doc links and run JPlag |
| `POST /api/cohorts/{cohort_id}/batches/{batch_id}/status` | `POST` | Update pair review status (`Pending`, `Confirmed`, `Cleared`) |
| `DELETE /api/cohorts/{cohort_id}/batches/{batch_id}` | `DELETE` | Delete a batch run and clear stored student files |

---

## 🐳 Docker & Production Deployment

### Building with Docker
```bash
docker build -t integrity-observatory .
docker run -d -p 8000:8000 --name plagiarism_app integrity-observatory
```

### Deploying on Render / Cloud
- Set **Build Command**: `pip install -r backend/requirements.txt`
- Set **Start Command**: `python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000`
- Ensure **Java Runtime** is available on the deployment environment.

---

## 🤝 10 Academy Autograder Integration

For automated runs triggered by 10 Academy's platform at assignment deadlines:
1. **Link Collection**: Post student submission URLs to `/api/cohorts/{cohort_id}/batches/{batch_id}/submissions`.
2. **Deadline Trigger**: Send a POST request to `/api/run-fetched` at deadline time.
3. **Staff Authentication**: Secure the dashboard using 10 Academy's existing OAuth2 / JWT authentication service.

---

## 📜 License
Internal academic integrity tool built for cohort analysis. Powered by open-source [JPlag](https://github.com/jplag/JPlag) AST parser.

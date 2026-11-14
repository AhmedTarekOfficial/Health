# 🛸 OrbitNexus — Autonomous Astronaut Health & Biosensor Intelligence System

> **NASA Space Apps Hackathon 2026** | Team OrbitNexus  
> **Master Documentation:** [ORBITNEXUS_MASTER_DOCUMENTATION.md](file:///c:/Users/OMEA/Downloads/OrbitNexus_NasaProject/ORBITNEXUS_MASTER_DOCUMENTATION.md)

<p align="center">
  <strong>Real-time AI-powered health telemetry monitoring, 5 edge machine learning models, certified spaceflight pathology mitigation, and hands-free aerospace voice AI for astronauts aboard the International Space Station and deep space exploration missions.</strong>
</p>

---

## 🌟 Overview

**OrbitNexus** is an autonomous, on-device biomedical intelligence system engineered specifically for long-duration spaceflight (ISS Expedition 71, Artemis, Mars transit). It combines **5 specialized machine learning models**, continuous **multi-modal wearable biosensor telemetry**, **spaceflight pathology clinical decision support (NASA SP-2018-639)**, **continuous online learning with concept drift detection**, and a **hands-free voice communication bus** with an aerospace medical officer (Groq Llama 3.3 / Qwen).

---

## 🏆 Key Capabilities

| Subsystem | Underlying Architecture | Dataset & Source | Metric & Performance |
|---|---|---|---|
| 🏃 **Physical Activity Recognition** | Random Forest GPU (100 Trees) | [PAMAP2 Dataset](https://archive.ics.uci.edu/dataset/231) (119K samples) | **99.62% Test Accuracy** (Macro F1: 0.994) |
| 🧠 **Cognitive Stress & Autonomic Tone** | XGBoost Multiclass Classifier | [WESAD Dataset](https://archive.ics.uci.edu/dataset/465) (HR, HRV, EDA, Temp, Resp) | **89.67% Test Accuracy** (Kappa: 0.862) |
| 🫀 **Continuous Pulse Wave HR** | Non-linear RF Regressor + Kalman | [BIDMC PPG & Respiration](https://physionet.org/content/bidmc/1.0.0/) (PhysioNet) | **MAE 1.10 BPM** (Pearson r: 0.988) |
| ⚠️ **Physiological Anomaly Detection** | Isolation Forest (Zero-Day Outlier) | ISS Biosuite Simulation Vital Space | Contamination 0.05 (Unsupervised) |
| 🧬 **Spaceflight Bio-Adaptation** | Support Vector Machine (RBF) | [NASA GeneLab OSD-178](https://osdr.nasa.gov/bio/repo/data/studies/OSD-178) | **100.0% Validation Accuracy** (AUC: 1.0) |
| 🩺 **Spaceflight Pathology Decision Support** | Multi-hazard Clinical Matrix | NASA SP-2018-639 / NASA HRP Standards | 6 Certified Flight Surgeon Hazard Tracks |
| 🔄 **Continuous Online Learning** | SGDClassifier (`partial_fit`) | Streaming real-time telemetry updates | Rolling Accuracy & Concept Drift Monitor |
| 🎙️ **Hands-Free Flight Audio Bus** | Web Speech API + Web Audio API | Live real-time typing & silence auto-stop | Audio Waveform Equalizer Visualizer |
| 🤖 **OrbitNexus Medical AI** | Llama 3.3 70B / Qwen via Groq | Real-time telemetry context injection | Sub-second latency clinical triage |

---

## 🏗️ System Architecture

```text
OrbitNexus/
├── ASTRA/
│   ├── models/                       # 5 Production-trained ML Models
│   │   ├── activity_model/           # activity.pkl + activity_meta.json (99.62% Acc)
│   │   ├── stress_model/             # stress.pkl + stress_meta.json (89.67% Acc)
│   │   ├── heart_model/              # heart.pkl + heart_meta.json (1.10 BPM MAE)
│   │   ├── anomaly_model/            # anomaly.pkl + anomaly_meta.json (IsolationForest)
│   │   └── space_bio_model/          # space_bio.pkl + space_bio_meta.json (NASA OSD-178)
│   └── website/
│       ├── app.py                    # Flask + SocketIO telemetry server & REST API
│       ├── astra_engine.py           # Unified inference engine for all 5 models
│       ├── sensor_pipeline.py        # Canonical sensor normalization & simulation
│       ├── llm_assistant.py          # Groq / Llama 3.3 70B Medical Officer
│       ├── templates/
│       │   └── index.html            # NASA Expedition 71 Flight Surgeon Console
│       └── src/                      # Modular ES6 Architecture (Dashboard, Checkin, Voice, Ambient)
├── tests/                            # 16 Automated Unit Tests (100% Passing)
├── start_orbitnexus.bat              # One-click Windows launch script
├── requirements.txt                  # Python dependencies
└── ORBITNEXUS_MASTER_DOCUMENTATION.md # Comprehensive system manual & API reference
```

---

## 🚀 Quick Launch Guide

### 1. Prerequisites
- Python 3.10, 3.11, or 3.12
- Modern web browser (Chrome, Edge, Firefox)

### 2. Launch the System
Simply double-click:
```bash
start_orbitnexus.bat
```
Or run from terminal:
```powershell
pip install -r requirements.txt
python ASTRA/website/app.py
```

### 3. Open Mission Control
Open your web browser and navigate to:
```text
http://localhost:5000
```

---

## 🧪 Running the Test Suite

Run the full automated test suite (16 tests):
```powershell
python -m unittest discover tests -v
```

---

## 🛰️ Open Science Credentials & Datasets

1. **PAMAP2 Physical Activity Monitoring**: 9 subjects, 18 activities, 3 IMUs (wrist, chest, ankle) sampled at 100 Hz.
2. **BIDMC PPG and Respiration Dataset**: PhysioNet non-invasive PPG pulse oximetry and respiratory rate benchmarks.
3. **WESAD Wearable Stress and Affect Detection**: Multimodal physiological data recorded under standardized cognitive stress stressors.
4. **NASA GeneLab OSD-178**: Microarray gene expression profiling of spaceflight adaptation vs. Earth ground control.

---

## 👥 Team OrbitNexus

Built with passion for deep-space exploration and astronaut crew health for the **NASA Space Apps Hackathon 2026**.

*"Ad astra per aspera — Through hardships to the stars."*

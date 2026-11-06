# Where should AI live?
### Finding a home in São Paulo by commute time

Tell the app **where you work**, **how you commute** (public transport or car) and **how many minutes you're willing to spend**. It recommends rental listings in São Paulo that fit your commute, groups neighborhoods by mobility and price, and lets you search through a chatbot running on a free, local LLM.

The project covers the full data science workflow:

1. **Web scraping** of rental listings
2. **SQL database** with PostgreSQL + PostGIS
3. **Urban mobility database** built from open data: GTFS + OpenStreetMap with r5py for transit, and OSRM plus a congestion model fitted on the Metrô Origem-Destino survey for car
4. **Machine learning**: PCA + K-Means clustering of areas using mobility features, and a KNN recommender
5. **API and app** with FastAPI and Streamlit
6. **Chatbot** using a local LLM (Ollama) with tool calling

Everything uses free tools and open data.

## Project status
Work in progress.

## How to run
*Coming soon.*

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements-lock.txt
```

## Disclaimer
This is an educational, non-commercial project. Listing data was collected at a low request rate for personal study, contains no personal data, and **is not published or redistributed** in this repository. This project is not affiliated with QuintoAndar.

// Where the NexusAI API runs.
//   ""  (default) -> the same site that serves this page: `python backend\run.py` locally, or a
//                    single Docker deployment that serves the web app and the API together.
//   "https://nexusai-api.onrender.com" -> a separate backend, when this frontend is on Vercel.
//                    That backend must list this site's address in API_CORS_ORIGINS.
window.NEXUSAI_CONFIG = {
  apiBase: "",
};

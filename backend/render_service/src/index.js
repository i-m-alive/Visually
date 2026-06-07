import express from 'express';

const app = express();
app.use(express.json({ limit: '10mb' }));

const PORT = process.env.PORT || 3001;

// Stub render service — the frontend renders charts in-browser via Recharts.
// This service exists for server-side image generation (exports/thumbnails),
// but canvas native binaries are not available on all platforms.
// Returns empty image_base64 so the pipeline continues without crashing.

app.post('/render', (req, res) => {
  const { query_plan } = req.body || {};
  const chart_type = query_plan?.chart_type || 'bar';
  const title = query_plan?.title || '';
  res.json({ image_base64: '', chart_type, title });
});

app.get('/health', (req, res) => {
  res.json({ status: 'ok', mode: 'stub' });
});

app.listen(PORT, () => {
  console.log(`Render service (stub) listening on port ${PORT}`);
});

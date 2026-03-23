import { test, expect, Page, Request, Response } from '@playwright/test';

const DASHBOARD_URL = '/projects/laptop/dashboards/summary';
const CONTAINERS_DASHBOARD_URL = '/projects/laptop/dashboards/containers';
const PROMETHEUS_URL = 'http://localhost:9090';
const PERSES_PROXY_BASE = 'http://localhost:8080/proxy/globaldatasources/prometheus';

// ── Helpers ──────────────────────────────────────────────────────────────────

async function prometheusQuery(query: string): Promise<number> {
  const url = `${PROMETHEUS_URL}/api/v1/query?query=${encodeURIComponent(query)}`;
  const response = await fetch(url);
  const json = await response.json();
  if (json.status !== 'success' || !json.data.result.length) {
    throw new Error(`Prometheus query returned no data: ${query}`);
  }
  return parseFloat(json.data.result[0].value[1]);
}

async function persesProxyQuery(query: string): Promise<number> {
  const url = `${PERSES_PROXY_BASE}/api/v1/query?query=${encodeURIComponent(query)}`;
  const response = await fetch(url);
  const json = await response.json();
  if (json.status !== 'success' || !json.data.result.length) {
    throw new Error(`Perses proxy query returned no data: ${query}`);
  }
  return parseFloat(json.data.result[0].value[1]);
}

// ── Layer 1: Prometheus has data ──────────────────────────────────────────────

test.describe('Layer 1: Prometheus data availability', () => {
  test('Prometheus is healthy', async () => {
    const response = await fetch(`${PROMETHEUS_URL}/-/healthy`);
    expect(response.status).toBe(200);
    const text = await response.text();
    expect(text).toContain('Healthy');
  });

  test('node_exporter target is UP', async () => {
    const response = await fetch(`${PROMETHEUS_URL}/api/v1/targets`);
    const json = await response.json();
    const nodeTarget = json.data.activeTargets.find((t: any) => t.labels.job === 'node');
    expect(nodeTarget, 'node job target not found').toBeTruthy();
    expect(nodeTarget.health).toBe('up');
  });

  test('CPU metrics exist', async () => {
    const val = await prometheusQuery('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)');
    expect(val).toBeGreaterThanOrEqual(0);
    expect(val).toBeLessThanOrEqual(100);
  });

  test('Memory metrics exist', async () => {
    const total = await prometheusQuery('node_memory_MemTotal_bytes');
    expect(total).toBeGreaterThan(0);
    const available = await prometheusQuery('node_memory_MemAvailable_bytes');
    expect(available).toBeGreaterThan(0);
    expect(available).toBeLessThan(total);
  });

  test('Load average metrics exist', async () => {
    const load1 = await prometheusQuery('node_load1');
    expect(load1).toBeGreaterThanOrEqual(0);
  });

  test('Disk I/O metrics exist', async () => {
    const read = await prometheusQuery('sum(rate(node_disk_read_bytes_total{device!~"loop.+"}[2m]))');
    expect(read).toBeGreaterThanOrEqual(0);
  });

  test('Filesystem metrics exist', async () => {
    const response = await fetch(`${PROMETHEUS_URL}/api/v1/query?query=node_filesystem_size_bytes`);
    const json = await response.json();
    expect(json.status).toBe('success');
    expect(json.data.result.length, 'No filesystem metrics — collector may be broken').toBeGreaterThan(0);
  });

  test('Network metrics exist', async () => {
    const rx = await prometheusQuery('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.+|docker.+|br-.+"}[2m]))');
    expect(rx).toBeGreaterThanOrEqual(0);
  });

  test('Uptime metric exists', async () => {
    const uptime = await prometheusQuery('time() - node_boot_time_seconds');
    expect(uptime).toBeGreaterThan(0);
  });
});

// ── Layer 2: Perses proxy delivers data ──────────────────────────────────────

test.describe('Layer 2: Perses server-side proxy', () => {
  test('Perses is healthy', async () => {
    const response = await fetch('http://localhost:8080/api/v1/health');
    const json = await response.json();
    expect(json.version).toBeTruthy();
  });

  test('Perses datasource is configured', async () => {
    const response = await fetch('http://localhost:8080/api/v1/globaldatasources/prometheus');
    expect(response.status).toBe(200);
    const json = await response.json();
    expect(json.spec.plugin.kind).toBe('PrometheusDatasource');
    expect(json.spec.plugin.spec.proxy.spec.url).toBe('http://prometheus:9090');
  });

  test('Perses proxy delivers CPU data', async () => {
    const val = await persesProxyQuery('100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)');
    expect(val).toBeGreaterThanOrEqual(0);
    expect(val).toBeLessThanOrEqual(100);
  });

  test('Perses proxy delivers memory data', async () => {
    const val = await persesProxyQuery('node_memory_MemTotal_bytes');
    expect(val).toBeGreaterThan(0);
  });

  test('Perses proxy delivers range query data', async () => {
    const start = Math.floor(Date.now() / 1000) - 3600;
    const end = Math.floor(Date.now() / 1000);
    const url = `${PERSES_PROXY_BASE}/api/v1/query_range?query=node_load1&start=${start}&end=${end}&step=60`;
    const response = await fetch(url);
    const json = await response.json();
    expect(json.status).toBe('success');
    expect(json.data.result.length).toBeGreaterThan(0);
    expect(json.data.result[0].values.length).toBeGreaterThan(0);
  });

  test('Perses dashboard exists', async () => {
    const response = await fetch('http://localhost:8080/api/v1/projects/laptop/dashboards/summary');
    expect(response.status).toBe(200);
    const json = await response.json();
    expect(json.metadata.name).toBe('summary');
    expect(Object.keys(json.spec.panels).length).toBeGreaterThan(0);
  });

  test('All 9 Bluefin Instrumentation dashboards exist', async () => {
    const dashboards = [
      'summary', 'cpu', 'memory', 'disk', 'network',
      'battery-power', 'thermals', 'health', 'containers',
    ];
    for (const name of dashboards) {
      const response = await fetch(`http://localhost:8080/api/v1/projects/laptop/dashboards/${name}`);
      expect(response.status, `Dashboard '${name}' not found`).toBe(200);
    }
  });

  test('Podman exporter target is UP', async () => {
    const response = await fetch(`${PROMETHEUS_URL}/api/v1/targets`);
    const json = await response.json();
    const podmanTarget = json.data.activeTargets.find((t: any) => t.labels.job === 'podman');
    expect(podmanTarget, 'podman job target not found').toBeTruthy();
    expect(podmanTarget.health).toBe('up');
  });
});

// ── Layer 3: Browser renders charts with data ─────────────────────────────────

test.describe('Layer 3: Browser renders charts with data', () => {
  test.use({ baseURL: 'http://localhost:8080' });

  let networkRequests: { url: string; status: number }[] = [];

  test('dashboard page loads without errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });

    networkRequests = [];
    page.on('response', (response) => {
      networkRequests.push({ url: response.url(), status: response.status() });
    });

    await page.goto(DASHBOARD_URL);
    await page.waitForLoadState('networkidle');

    // Log all failed requests for diagnosis
    const failed = networkRequests.filter(r => r.status >= 400);
    if (failed.length > 0) {
      console.log('FAILED REQUESTS:');
      failed.forEach(r => console.log(`  ${r.status} ${r.url}`));
    }
    expect(failed.filter(r => !r.url.includes('.gz')), `Failed requests: ${JSON.stringify(failed)}`).toHaveLength(0);
  });

  test('dashboard makes proxy requests to Prometheus', async ({ page }) => {
    const proxyRequests: string[] = [];
    const directRequests: string[] = [];

    page.on('request', (req) => {
      const url = req.url();
      if (url.includes('/proxy/') && url.includes('prometheus')) proxyRequests.push(url);
      if (url.includes('localhost:9090')) directRequests.push(url);
    });

    await page.goto(DASHBOARD_URL);
    await page.waitForLoadState('networkidle');
    // Give panels time to fire queries
    await page.waitForTimeout(3000);

    console.log(`Proxy requests: ${proxyRequests.length}`);
    console.log(`Direct Prometheus requests: ${directRequests.length}`);
    proxyRequests.slice(0, 3).forEach(u => console.log('  proxy:', u));
    directRequests.slice(0, 3).forEach(u => console.log('  direct:', u));

    const totalDataRequests = proxyRequests.length + directRequests.length;
    expect(totalDataRequests, 'No Prometheus queries made by the browser at all').toBeGreaterThan(0);
  });

  test('CPU panel shows rendered chart content', async ({ page }) => {
    await page.goto(DASHBOARD_URL);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(3000);

    // Look for SVG paths (actual chart lines drawn by echarts/canvas)
    const svgPaths = page.locator('svg path[d]');
    const canvases = page.locator('canvas');

    const svgCount = await svgPaths.count();
    const canvasCount = await canvases.count();
    console.log(`SVG paths: ${svgCount}, Canvases: ${canvasCount}`);

    expect(svgCount + canvasCount, 'No chart elements rendered — panels are empty').toBeGreaterThan(0);
  });

  test('no "No data" messages visible on dashboard', async ({ page }) => {
    await page.goto(DASHBOARD_URL);
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(3000);

    const noDataText = page.getByText(/no data/i);
    const count = await noDataText.count();
    console.log(`"No data" messages found: ${count}`);

    // Take screenshot for reference
    await page.screenshot({ path: 'test-results/dashboard.png', fullPage: true });

    expect(count, `Found ${count} "No data" messages on the dashboard`).toBe(0);
  });
});

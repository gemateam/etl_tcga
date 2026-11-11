(() => {
  const stageNodes = [
    ['extract_cases', 'Extracción'],
    ['extract_files_manifest', 'Manifiesto expresión'],
    ['extract_maf_manifest', 'Manifiesto MAF'],
    ['download_expressions', 'Descarga expresión'],
    ['bronze_cases', 'Bronze casos'],
    ['bronze_files', 'Bronze archivos'],
    ['bronze_expressions', 'Bronze expresión'],
    ['bronze_mutations', 'Bronze mutaciones'],
    ['silver_clinical', 'Silver clínica'],
    ['silver_case_expressions', 'Silver expresión'],
    ['silver_mutations', 'Silver mutaciones'],
    ['gold_top_20_diagnosis', 'Top diagnósticos'],
    ['gold_sample_summary', 'Resumen muestras'],
    ['gold_gene_stats', 'Estadísticas génicas'],
    ['gold_gene_dim', 'Dimensión genes'],
    ['gold_sample_marker_values', 'Marcadores'],
    ['gold_cx_pivot', 'Pivote de expresión']
  ];
  const edges = [
    ['extract_cases', 'bronze_cases'],
    ['extract_files_manifest', 'bronze_files'],
    ['extract_files_manifest', 'download_expressions'],
    ['extract_maf_manifest', 'bronze_mutations'],
    ['download_expressions', 'bronze_expressions'],
    ['bronze_cases', 'silver_clinical'],
    ['bronze_cases', 'silver_case_expressions'],
    ['bronze_files', 'silver_case_expressions'],
    ['bronze_expressions', 'silver_case_expressions'],
    ['bronze_mutations', 'silver_mutations'],
    ['silver_clinical', 'gold_top_20_diagnosis'],
    ['silver_clinical', 'gold_sample_summary'],
    ['silver_case_expressions', 'gold_gene_stats'],
    ['silver_case_expressions', 'gold_gene_dim'],
    ['silver_case_expressions', 'gold_sample_marker_values'],
    ['silver_case_expressions', 'gold_cx_pivot']
  ];
  const layerX = {landing: 0.03, bronze: 0.29, silver: 0.55, gold: 0.81};
  const stepLayer = key => key.startsWith('extract_') || key === 'download_expressions'
    ? 'landing'
    : key.startsWith('bronze_')
      ? 'bronze'
      : key.startsWith('silver_')
        ? 'silver'
        : 'gold';
  const palette = {
    success: '#15803d',
    failed: '#dc2626',
    running: '#d97706',
    pending: '#64748b',
    landing: '#0f766e',
    bronze: '#d97706',
    silver: '#2563eb',
    gold: '#7c3aed'
  };
  const baseLayout = {
    margin: {t: 20, r: 18, b: 55, l: 60},
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: {color: '#1e293b', family: 'Segoe UI, sans-serif'},
    showlegend: false
  };
  const config = {responsive: true, displaylogo: false};

  function showEmpty(id, message) {
    const element = document.getElementById(id);
    if (element) element.innerHTML = `<p class="muted">${message}</p>`;
  }

  function plot(id, data, layout, emptyMessage = 'Sin datos para esta visualización.') {
    const element = document.getElementById(id);
    if (!element) return;
    if (!data || !data.length) {
      showEmpty(id, emptyMessage);
      return;
    }
    Plotly.newPlot(element, data, {...baseLayout, ...layout}, config);
  }

  function renderDag(history) {
    const latest = new Map();
    history.forEach(run => {
      const key = run.step;
      if (!latest.has(key)) latest.set(key, run);
    });
    const positions = new Map(stageNodes.map(([key], index) => [key, index]));
    const layerCounts = {};
    const layerIndexes = {};
    stageNodes.forEach(([key]) => {
      const layer = stepLayer(key);
      layerCounts[layer] = (layerCounts[layer] || 0) + 1;
    });
    const nodeX = [];
    const nodeY = [];
    stageNodes.forEach(([key]) => {
      const layer = stepLayer(key);
      const index = layerIndexes[layer] || 0;
      layerIndexes[layer] = index + 1;
      nodeX.push(layerX[layer]);
      nodeY.push((index + 1) / (layerCounts[layer] + 1));
    });
    const statusColor = run => palette[run?.status] || palette.pending;
    const nodeColors = stageNodes.map(([key]) => statusColor(latest.get(key)));
    const linkSources = [];
    const linkTargets = [];
    edges.forEach(([source, target]) => {
      if (positions.has(source) && positions.has(target)) {
        linkSources.push(positions.get(source));
        linkTargets.push(positions.get(target));
      }
    });
    const nodeRuns = stageNodes.map(([key]) => latest.get(key));
    const customdata = nodeRuns.map(run => run
      ? `${run.status} · ${Number(run.rows_out || 0).toLocaleString('es')} filas · ${Number(run.duration_seconds || 0).toFixed(1)} s${run.error ? ` · ${run.error}` : ''}`
      : 'Sin ejecución registrada');
    plot('pipeline-dag', [{
      type: 'sankey',
      arrangement: 'snap',
      node: {
        label: stageNodes.map(([, label]) => label),
        color: nodeColors,
        x: nodeX,
        y: nodeY,
        customdata,
        hovertemplate: '%{label}<br>%{customdata}<extra></extra>',
        pad: 18,
        thickness: 18,
        line: {color: '#ffffff', width: 1}
      },
      link: {
        source: linkSources,
        target: linkTargets,
        value: linkSources.map(() => 1),
        color: 'rgba(100,116,139,0.22)',
        hovertemplate: '%{source.label} → %{target.label}<extra></extra>'
      }
    }], {
      margin: {t: 12, r: 12, b: 12, l: 12},
      height: window.matchMedia('(max-width: 640px)').matches ? 700 : 500,
      font: {color: '#1e293b', size: 11}
    }, 'Todavía no hay pasos ETL registrados.');
  }

  function renderDuration(history) {
    const steps = [...new Set(history.map(run => run.step))];
    const data = steps.map(step => {
      const rows = history.filter(run => run.step === step).sort((a, b) => a.started_at.localeCompare(b.started_at));
      return {
        type: 'scatter',
        mode: 'lines+markers',
        name: step,
        x: rows.map(run => run.started_at),
        y: rows.map(run => Number(run.duration_seconds || 0)),
        marker: {color: rows.map(run => palette[run.status] || palette.pending), size: 8},
        customdata: rows.map(run => [run.status, run.project_id || '—', run.rows_out || 0]),
        hovertemplate: '%{x}<br>%{fullData.name}<br>%{y:.1f} s<br>%{customdata[0]} · %{customdata[2]:,} filas<extra></extra>'
      };
    });
    plot('pipeline-duration', data, {
      xaxis: {title: 'Inicio', type: 'date'},
      yaxis: {title: 'Segundos', rangemode: 'tozero'},
      legend: {orientation: 'h', y: -0.3},
      margin: {t: 18, r: 12, b: 75, l: 60}
    });
  }

  function renderDaily(rows) {
    if (!rows.length) return showEmpty('pipeline-history', 'Sin ejecuciones en los últimos 30 días.');
    const dates = rows.map(row => row.run_date);
    plot('pipeline-history', [
      {type: 'bar', name: 'Éxito', x: dates, y: rows.map(row => row.success), marker: {color: palette.success}},
      {type: 'bar', name: 'Fallido', x: dates, y: rows.map(row => row.failed), marker: {color: palette.failed}},
      {type: 'bar', name: 'En curso', x: dates, y: rows.map(row => row.running), marker: {color: palette.running}}
    ], {barmode: 'stack', xaxis: {title: 'Día'}, yaxis: {title: 'Ejecuciones', rangemode: 'tozero'}, legend: {orientation: 'h', y: -0.28}});
  }

  function renderRows(history) {
    const latestByStep = new Map();
    history.forEach(run => {
      const key = `${run.step}:${run.project_id || ''}`;
      if (!latestByStep.has(key)) latestByStep.set(key, run);
    });
    const rows = [...latestByStep.values()].sort((a, b) => a.layer.localeCompare(b.layer) || a.step.localeCompare(b.step));
    plot('pipeline-rows', [{
      type: 'bar',
      orientation: 'h',
      y: rows.map(run => `${run.step} · ${run.layer}`).reverse(),
      x: rows.map(run => Number(run.rows_out || 0)).reverse(),
      marker: {color: rows.map(run => palette[run.layer] || palette.pending).reverse()},
      customdata: rows.map(run => [run.status, run.project_id || '—']).reverse(),
      hovertemplate: '%{y}<br>%{x:,} filas<br>%{customdata[0]} · %{customdata[1]}<extra></extra>'
    }], {
      margin: {t: 12, r: 18, b: 40, l: 180},
      xaxis: {title: 'Filas de salida', rangemode: 'tozero'},
      yaxis: {automargin: true}
    }, 'No hay conteos de filas registrados.');
  }

  function renderQuality(rows) {
    if (!rows.length) return showEmpty('pipeline-quality', 'Sin controles de calidad registrados.');
    const severities = [...new Set(rows.map(row => row.severity))];
    plot('pipeline-quality', [
      {type: 'bar', name: 'Aprobados', x: severities, y: severities.map(severity => rows.filter(row => row.severity === severity && row.passed).reduce((sum, row) => sum + row.checks, 0)), marker: {color: palette.success}},
      {type: 'bar', name: 'Fallidos', x: severities, y: severities.map(severity => rows.filter(row => row.severity === severity && !row.passed).reduce((sum, row) => sum + row.checks, 0)), marker: {color: palette.failed}}
    ], {barmode: 'group', xaxis: {title: 'Severidad'}, yaxis: {title: 'Controles', rangemode: 'tozero'}, legend: {orientation: 'h', y: -0.28}});
  }

  function renderBiology(data) {
    plot('biology-stages', [{
      type: 'bar', x: data.stages.map(row => row.stage), y: data.stages.map(row => row.cases),
      marker: {color: '#0f766e'}, hovertemplate: '%{x}<br>%{y:,} casos<extra></extra>'
    }], {xaxis: {title: 'Estadio', tickangle: -25}, yaxis: {title: 'Casos', rangemode: 'tozero'}, margin: {t: 15, r: 10, b: 75, l: 55}});
    plot('biology-ages', [{
      type: 'bar', x: data.ages.map(row => row.age_group), y: data.ages.map(row => row.cases),
      marker: {color: '#2563eb'}, hovertemplate: '%{x}<br>%{y:,} casos<extra></extra>'
    }], {xaxis: {title: 'Edad al índice'}, yaxis: {title: 'Casos', rangemode: 'tozero'}});
    plot('biology-mutations', [{
      type: 'bar', orientation: 'h',
      y: data.top_mutations.map(row => row.gene).reverse(),
      x: data.top_mutations.map(row => row.cases).reverse(),
      marker: {color: '#b45309'},
      customdata: data.top_mutations.map(row => row.mutations).reverse(),
      hovertemplate: '%{y}<br>%{x} casos · %{customdata} variantes<extra></extra>'
    }], {xaxis: {title: 'Casos con variante'}, margin: {t: 15, r: 12, b: 45, l: 85}});

    const matrix = new Map();
    const genes = [...new Set(data.oncoplot.map(row => row.gene))];
    const cases = [...new Map(data.oncoplot.map(row => [row.case_id, row.submitter_id || row.case_id])).entries()];
    data.oncoplot.forEach(row => matrix.set(`${row.gene}:${row.case_id}`, Number(row.mutations) > 0 ? 1 : 0));
    plot('biology-oncoplot', genes.length && cases.length ? [{
      type: 'heatmap',
      x: cases.map(([, label]) => label),
      y: genes,
      z: genes.map(gene => cases.map(([caseId]) => matrix.get(`${gene}:${caseId}`) || 0)),
      colorscale: [[0, '#f1f5f9'], [1, '#dc2626']],
      showscale: false,
      hovertemplate: '%{y} · %{x}<br>%{z} mutación detectada<extra></extra>'
    }] : [], {margin: {t: 10, r: 10, b: 100, l: 80}, xaxis: {tickangle: -55, automargin: true}, yaxis: {automargin: true}}, 'Sin mutaciones disponibles para construir el oncoplot.');

    plot('biology-expression', [{
      type: 'bar', x: data.expression.map(row => row.gene), y: data.expression.map(row => row.median_tpm),
      marker: {color: '#7c3aed'}, customdata: data.expression.map(row => row.samples),
      hovertemplate: '%{x}<br>Mediana %{y:.2f} TPM<br>%{customdata:,} muestras<extra></extra>'
    }], {xaxis: {title: 'Gen'}, yaxis: {title: 'TPM mediano', rangemode: 'tozero'}});
  }

  async function init() {
    if (!window.Plotly) return;
    const project = new URLSearchParams(window.location.search).get('project');
    const query = project ? `?project=${encodeURIComponent(project)}` : '';
    try {
      const response = await fetch(`/api/pipeline/visualization${query}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      renderDag(data.history || []);
      renderDuration(data.history || []);
      renderDaily(data.daily_runs || []);
      renderRows(data.history || []);
      renderQuality(data.quality || []);
      renderBiology(data);
    } catch (error) {
      document.querySelectorAll('.pipeline-dashboard .chart').forEach(element => {
        showEmpty(element.id, 'No se pudieron cargar las visualizaciones. Revisa la conexión con PostgreSQL.');
      });
    }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
'use strict';

const DATA = 'data/';
const DIAS_SEM = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

const $ = (s) => document.querySelector(s);
const cache = {};   // dia -> json

init();

async function init() {
  try {
    const idx = await getJSON(DATA + 'index.json');
    pintarActualizado(idx.actualizado);
    const dias = idx.dias;
    if (!dias.length) { $('#lista').innerHTML = '<p class="vacio">No hay partidos programados.</p>'; return; }
    const hoy = new Date().toISOString().slice(0, 10);
    let sel = dias.find((d) => d >= hoy) || dias[0];
    pintarDias(dias, sel, (d) => mostrarDia(d, dias));
    mostrarDia(sel, dias);
  } catch (e) {
    $('#lista').innerHTML = '<p class="vacio">No se pudo cargar la información.<br>Revisá tu conexión.</p>';
  }
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
}

async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-cache' });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

function pintarActualizado(iso) {
  const d = new Date(iso);
  $('#actualizado').textContent = 'act. ' + d.toLocaleString('es', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function etiquetaDia(iso) {
  const d = new Date(iso + 'T12:00:00');
  const hoy = new Date(); hoy.setHours(12, 0, 0, 0);
  const difd = Math.round((d - hoy) / 86400000);
  if (difd === 0) return { t: 'Hoy', n: `${d.getDate()} ${MESES[d.getMonth()]}` };
  if (difd === 1) return { t: 'Mañana', n: `${d.getDate()} ${MESES[d.getMonth()]}` };
  return { t: DIAS_SEM[d.getDay()], n: `${d.getDate()} ${MESES[d.getMonth()]}` };
}

function pintarDias(dias, sel, onPick) {
  const nav = $('#dias');
  nav.innerHTML = '';
  dias.forEach((iso) => {
    const { t, n } = etiquetaDia(iso);
    const b = document.createElement('button');
    b.className = 'dia' + (iso === sel ? ' sel' : '');
    b.innerHTML = `${t}<span class="n">${n}</span>`;
    b.onclick = () => {
      nav.querySelectorAll('.dia').forEach((x) => x.classList.remove('sel'));
      b.classList.add('sel');
      b.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
      onPick(iso);
    };
    nav.appendChild(b);
  });
  const activo = nav.querySelector('.sel');
  if (activo) activo.scrollIntoView({ inline: 'center', block: 'nearest' });
}

async function mostrarDia(iso) {
  const cont = $('#lista');
  cont.innerHTML = '<p class="cargando">Cargando…</p>';
  let data;
  try {
    data = cache[iso] || (cache[iso] = await getJSON(DATA + iso + '.json'));
  } catch (e) {
    cont.innerHTML = '<p class="vacio">No hay datos para este día.</p>';
    return;
  }
  const porComp = {};
  data.partidos.forEach((p) => (porComp[p.competicion] ||= []).push(p));

  cont.innerHTML = '';
  Object.keys(porComp).forEach((comp) => {
    const h = document.createElement('div');
    h.className = 'comp';
    h.textContent = comp;
    cont.appendChild(h);
    porComp[comp].forEach((p) => cont.appendChild(tarjeta(p)));
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function hora(iso) {
  return new Date(iso).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });
}

function pct(x) { return Math.round(x * 100) + '%'; }

function tarjeta(p) {
  const el = document.createElement('div');
  el.className = 'match';
  const pr = p.prediccion;

  if (!pr) {
    el.innerHTML = `
      <div class="fila1">
        <span class="hora">${hora(p.fecha_utc)}</span>
        <span class="equipos">${p.local} <span class="v">vs</span> ${p.visitante}</span>
      </div>
      <div class="sindatos">Sin datos suficientes para analizar este partido.</div>`;
    return el;
  }

  const [g1, g2] = pr.goles_esperados;
  const badge = pr.confianza === 'baja'
    ? '<span class="badge">datos limitados</span>' : '';

  el.innerHTML = `
    <div class="fila1">
      <span class="hora">${hora(p.fecha_utc)}</span>
      <span class="equipos">${p.local} <span class="v">vs</span> ${p.visitante}</span>
      ${badge}
    </div>
    <div class="barra">
      <i class="b1" style="width:${pr.prob_1 * 100}%"></i>
      <i class="bx" style="width:${pr.prob_X * 100}%"></i>
      <i class="b2" style="width:${pr.prob_2 * 100}%"></i>
    </div>
    <div class="pcts">
      <span>1 <b>${pct(pr.prob_1)}</b></span>
      <span>X <b>${pct(pr.prob_X)}</b></span>
      <span><b>${pct(pr.prob_2)}</b> 2</span>
    </div>
    <div class="resumen">${pr.resumen}</div>
    <div class="detalle">
      <div class="grid">
        <div><div class="k">Goles esperados</div>${g1.toFixed(2)} – ${g2.toFixed(2)}</div>
        <div><div class="k">Más de 2.5 goles</div>${pct(pr.prob_over_2_5)}</div>
        <div><div class="k">Ambos marcan</div>${pct(pr.prob_btts)}</div>
        <div><div class="k">Cuota justa 1 / X / 2</div>${pr.cuotas_justas.join(' / ')}</div>
      </div>
      <div class="marcadores">
        ${pr.marcadores.map(([m, q]) => `<span>${m} · ${pct(q)}</span>`).join('')}
      </div>
    </div>`;

  el.onclick = () => el.classList.toggle('abierto');
  return el;
}

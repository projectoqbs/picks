'use strict';

const DATA = 'data/';
const DIAS_SEM = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

const $ = (s) => document.querySelector(s);
const cache = {};          // dia -> json partidos
const cacheTablas = {};    // codigo -> json tabla
let IDX = null;
let vista = 'partidos';

init();

async function init() {
  try {
    IDX = await getJSON(DATA + 'index.json');
    pintarActualizado(IDX.actualizado);
    if (!IDX.dias.length) { $('#lista').innerHTML = '<p class="vacio">No hay partidos programados.</p>'; return; }
    const hoy = new Date().toISOString().slice(0, 10);
    const sel = IDX.dias.find((d) => d >= hoy) || IDX.dias[0];
    pintarDias(IDX.dias, sel);
    pintarLigas(IDX.tablas || []);
    mostrarDia(sel);
  } catch (e) {
    $('#lista').innerHTML = '<p class="vacio">No se pudo cargar la información.<br>Revisá tu conexión.</p>';
  }

  document.querySelectorAll('.vista').forEach((b) => {
    b.onclick = () => cambiarVista(b.dataset.v);
  });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
    let recargando = false;
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (recargando) return;
      recargando = true;
      location.reload();
    });
  }
}

function cambiarVista(v) {
  vista = v;
  document.querySelectorAll('.vista').forEach((b) => b.classList.toggle('sel', b.dataset.v === v));
  $('#dias').hidden = v !== 'partidos';
  $('#ligas').hidden = v !== 'tablas';
  if (v === 'tablas') {
    const activa = $('#ligas .liga.sel');
    mostrarTabla(activa ? activa.dataset.c : (IDX.tablas || [])[0]?.codigo);
  } else {
    const activo = $('#dias .dia.sel');
    if (activo) mostrarDia(activo.dataset.d);
  }
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

function pintarDias(dias, sel) {
  const nav = $('#dias');
  nav.innerHTML = '';
  dias.forEach((iso) => {
    const { t, n } = etiquetaDia(iso);
    const b = document.createElement('button');
    b.className = 'dia' + (iso === sel ? ' sel' : '');
    b.dataset.d = iso;
    b.innerHTML = `${t}<span class="n">${n}</span>`;
    b.onclick = () => {
      nav.querySelectorAll('.dia').forEach((x) => x.classList.remove('sel'));
      b.classList.add('sel');
      b.scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
      mostrarDia(iso);
    };
    nav.appendChild(b);
  });
  nav.querySelector('.sel')?.scrollIntoView({ inline: 'center', block: 'nearest' });
}

function pintarLigas(tablas) {
  const nav = $('#ligas');
  nav.innerHTML = '';
  tablas.forEach((t, i) => {
    const b = document.createElement('button');
    b.className = 'liga' + (i === 0 ? ' sel' : '');
    b.dataset.c = t.codigo;
    b.textContent = t.nombre;
    b.onclick = () => {
      nav.querySelectorAll('.liga').forEach((x) => x.classList.remove('sel'));
      b.classList.add('sel');
      mostrarTabla(t.codigo);
    };
    nav.appendChild(b);
  });
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

async function mostrarTabla(codigo) {
  const cont = $('#lista');
  if (!codigo) { cont.innerHTML = '<p class="vacio">No hay tablas todavía.</p>'; return; }
  cont.innerHTML = '<p class="cargando">Cargando…</p>';
  let data;
  try {
    data = cacheTablas[codigo] || (cacheTablas[codigo] = await getJSON(DATA + `tabla_${codigo}.json`));
  } catch (e) {
    cont.innerHTML = '<p class="vacio">No hay tabla para esta competición.</p>';
    return;
  }
  const sig = (x) => (x >= 0 ? '+' : '') + x.toFixed(2);
  const filas = data.equipos.map((e, i) => `
    <tr class="${e.fiable ? '' : 'floja'}">
      <td class="pos">${i + 1}</td>
      <td class="nombre">${e.equipo}${e.fiable ? '' : ' <span class="mini">·pocos datos</span>'}</td>
      <td class="num neto">${sig(e.neto)}</td>
      <td class="num">${sig(e.ataque)}</td>
      <td class="num">${sig(e.defensa)}</td>
    </tr>`).join('');
  cont.innerHTML = `
    <div class="comp">${data.competicion} · fuerza del modelo</div>
    <div class="tablawrap">
      <table class="tabla">
        <thead><tr><th></th><th>Equipo</th><th>Neto</th><th>Atq</th><th>Def</th></tr></thead>
        <tbody>${filas}</tbody>
      </table>
    </div>
    <p class="leyenda">Neto = ataque + defensa; cuánto más alto, más fuerte el equipo
    (defensa alta = encaja menos). Es forma estadística de temporada, no el pronóstico
    de un partido.</p>`;
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function hora(iso) {
  return new Date(iso).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' });
}

function pct(x) { return Math.round(x * 100) + '%'; }

function puntos(racha) {
  const map = { G: 'pg', E: 'pe', P: 'pp' };
  return racha.map((r) => `<i class="${map[r]}" title="${r}"></i>`).join('');
}

function bloqueForma(nombre, f) {
  if (!f || !f.pj) return `<div class="forma"><b>${nombre}</b><span class="mini">sin partidos recientes</span></div>`;
  return `<div class="forma">
    <b>${nombre}</b>
    <span class="puntos">${puntos(f.racha)}</span>
    <span class="mini">${f.g}G ${f.e}E ${f.p}P · goles ${f.gf}-${f.gc}</span>
  </div>`;
}

function bloqueFuerza(local, visitante, fl, fv) {
  if (!fl || !fv) return '';
  const fila = (label, a, b) => `
    <div class="frow">
      <span class="fval">${a >= 0 ? '+' : ''}${a.toFixed(2)}</span>
      <span class="flab">${label}</span>
      <span class="fval">${b >= 0 ? '+' : ''}${b.toFixed(2)}</span>
    </div>`;
  return `<div class="fuerza">
    <div class="fhead"><span>${local}</span><span>${visitante}</span></div>
    ${fila('Ataque', fl.ataque, fv.ataque)}
    ${fila('Defensa', fl.defensa, fv.defensa)}
    ${fila('Neto', fl.neto, fv.neto)}
  </div>`;
}

function bloqueH2H(h2h) {
  if (!h2h || !h2h.length) return '';
  const filas = h2h.map((m) => `
    <div class="h2hrow">
      <span class="h2hfecha">${m.fecha}</span>
      <span>${m.local} ${m.goles_local}-${m.goles_visitante} ${m.visitante}</span>
    </div>`).join('');
  return `<div class="h2h"><div class="k">Cara a cara</div>${filas}</div>`;
}

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
  const an = p.analisis || {};

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
      ${bloqueFuerza(p.local, p.visitante, an.fuerza_local, an.fuerza_visitante)}
      <div class="formas">
        ${bloqueForma(p.local, an.forma_local)}
        ${bloqueForma(p.visitante, an.forma_visitante)}
      </div>
      ${bloqueH2H(an.cara_a_cara)}
    </div>`;

  el.onclick = () => el.classList.toggle('abierto');
  return el;
}

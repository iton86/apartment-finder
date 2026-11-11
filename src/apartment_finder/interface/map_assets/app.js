/* Unit-only markers deliberately use one interior anchor per planning unit. */
"use strict";
const $ = (id) => document.getElementById(id);
const map = L.map("map", {zoomControl:true}).setView([42.697,23.322],12);
const basemap = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · Boundaries: SofiaPlan'
}).addTo(map);
map.attributionControl.setPrefix("Leaflet");
const boundaries = L.geoJSON(null, {style,onEachFeature:(feature,layer)=>{
  layer.bindTooltip(element("span",`${feature.properties.name} · unit ${feature.properties.id}`));
  layer.on("click",()=>chooseUnit(feature.properties.id));
  unitLayers.set(feature.properties.id,layer);
}});
const pins = L.layerGroup().addTo(map);
let catalogue = null, selected = null, unitLayers = new Map(), eligible = [], adsByUnit = new Map();
function element(tag,text,className) {
  const node=document.createElement(tag); if(text!==undefined) node.textContent=text;
  if(className) node.className=className; return node;
}
function style(feature) {
  const active=selected===feature.properties.id;
  const hasAds=adsByUnit.has(feature.properties.id);
  return {color:active?"#135c45":"#4c8067",weight:active?3:1,fillColor:active?"#3d8e65":hasAds?"#86b87d":"#bfd2ae",fillOpacity:active?.27:hasAds?.2:.08};
}
function chooseUnit(id,zoom=true) {
  selected=id; const layer=unitLayers.get(id);
  if(layer && zoom) map.fitBounds(layer.getBounds(),{padding:[45,70],maxZoom:15});
  boundaries.setStyle(style); if(layer) layer.bringToFront(); render();
}
function matchingAds() {
  const minimum=Number($("confidence").value)/100;
  return catalogue.ads.filter(ad=>(catalogue.preview_mode && ad.basis==="candidate_preview") || ad.basis==="human_reviewed" || ad.confidence>=minimum);
}
function adCard(ad) {
  const card=element("article",undefined,"ad-card");
  card.append(element("span",ad.basis==="candidate_preview"?"Unreviewed candidate · not accepted":ad.basis==="human_reviewed"?"Human reviewed":`${Math.round(ad.confidence*100)}% unit confidence`,"ad-badge"));
  card.append(element("h3",ad.title));
  if(ad.price) card.append(element("p",`${Number(ad.price).toLocaleString()} ${ad.currency||""}`,"ad-price"));
  card.append(element("p","Approximate · assigned to this planning unit"));
  const list=element("ul"); for(const evidence of ad.evidence) list.append(element("li",evidence)); card.append(list);
  if(ad.url) {const link=element("a","View original ad ↗");link.href=ad.url;link.target="_blank";link.rel="noopener noreferrer";card.append(link);}
  return card;
}
function render() {
  if(!catalogue) return;
  eligible=matchingAds(); adsByUnit=new Map();
  for(const ad of eligible) {
    if(!adsByUnit.has(ad.planning_unit_id)) adsByUnit.set(ad.planning_unit_id,[]);
    adsByUnit.get(ad.planning_unit_id).push(ad);
  }
  const query=$("search").value.trim().toLocaleLowerCase();
  $("ad-count").textContent=eligible.length;
  $("ad-count").nextElementSibling.textContent=catalogue.preview_mode?"preview ads":"eligible ads";
  $("confidence-value").textContent=catalogue.preview_mode?"Unrated":`${$("confidence").value}%`;
  $("clear").hidden=selected===null;
  const chosen=catalogue.units.features.find(f=>f.properties.id===selected);
  $("map-selection").textContent=chosen?chosen.properties.name:"The whole city, one unit at a time.";
  $("results-title").textContent=chosen?`Unit ${chosen.properties.id} · ads`:"Explore the units";
  const results=$("results");results.replaceChildren();
  let visibleUnits=catalogue.units.features.filter(f=>!query || `${f.properties.id} ${f.properties.name}`.toLocaleLowerCase().includes(query) || (adsByUnit.get(f.properties.id)||[]).some(ad=>ad.title.toLocaleLowerCase().includes(query)));
  if(chosen) {
    const ads=(adsByUnit.get(selected)||[]).filter(ad=>!query||`${ad.title} ${chosen.properties.name} ${selected}`.toLocaleLowerCase().includes(query));
    for(const ad of ads) results.append(adCard(ad));
    $("status").textContent=ads.length?`${ads.length} ${catalogue.preview_mode?"unreviewed preview":"eligible"} ad${ads.length===1?"":"s"}. Marker shows the unit only.`:"No eligible ads in this unit. Try another unit or refresh after adding accepted assignments.";
  } else {
    visibleUnits.sort((a,b)=>(adsByUnit.get(b.properties.id)||[]).length-(adsByUnit.get(a.properties.id)||[]).length);
    for(const feature of visibleUnits) {
      const p=feature.properties,count=(adsByUnit.get(p.id)||[]).length;
      const card=element("button",undefined,"unit-card");card.type="button";
      const top=element("div",undefined,"card-top");top.append(element("span",`PLANNING UNIT ${p.id}`),element("span",`${count} ads`));
      card.append(top,element("div",p.name,"card-name"),element("div","Explore unit →","card-bottom"));card.onclick=()=>chooseUnit(p.id);results.append(card);
    }
    $("status").textContent=visibleUnits.length===0?"No matching units or ads.":eligible.length===0?"No accepted ads yet. Explore the boundaries while assignments are prepared.":`${visibleUnits.length} units · select a unit to inspect its ads.`;
  }
  pins.clearLayers();
  for(const feature of visibleUnits) {
    const p=feature.properties,ads=adsByUnit.get(p.id)||[];
    if(!ads.length) continue;
    const icon=L.divIcon({className:"unit-pin",html:`<div class="pin-count"><span>${ads.length}</span></div>`,iconSize:[34,34],iconAnchor:[17,32]});
    const marker=L.marker([p.marker[1],p.marker[0]],{icon,title:`${p.name}: ${ads.length} ads, unit-level location`});
    const popup=element("div");popup.append(element("strong",p.name),element("span",`${ads.length} ads · ${catalogue.preview_mode?"unreviewed suggested unit":"approximate unit-level location"}`));
    marker.bindPopup(popup);marker.on("click",()=>chooseUnit(p.id,false));pins.addLayer(marker);
  }
  boundaries.setStyle(style);
}
async function load() {
  $("reload").disabled=true;$("status").textContent="Loading your catalogue…";
  try {
    const response=await fetch("/api/map");const data=await response.json();
    if(!response.ok) throw new Error(data.error||"Map data unavailable.");
    catalogue=data;selected=null;boundaries.clearLayers();unitLayers=new Map();
    $("confidence").disabled=Boolean(data.preview_mode);
    document.querySelector(".local-badge").textContent=data.preview_mode?"Unreviewed preview":"Local catalogue";
    document.querySelector(".filter-note").textContent=data.preview_mode?
      "PREVIEW: real ads, unreviewed suggested units. Confidence is unknown. No accepted assignments are changed.":
      "Calibrated assignments only. Human-reviewed ads are always eligible.";
    document.querySelector(".accuracy-note p").textContent=data.preview_mode?
      "UNREVIEWED SUGGESTIONS · Marker shows a candidate unit, not a verified ad location.":
      "Unit-level locations · Markers represent a planning unit, not an apartment address.";

    boundaries.addData(data.units);
    if($("boundaries").checked) boundaries.addTo(map);
    $("unit-count").textContent=data.units.features.length;
    $("release").textContent=`Release ${data.release_id.slice(0,10)}${data.excluded_assignments?` · ${data.excluded_assignments} stale/unavailable assignments excluded`:""}`;
    render();
  } catch(error) {
    catalogue=null;eligible=[];selected=null;pins.clearLayers();boundaries.clearLayers();$("results").replaceChildren();
    $("unit-count").textContent="—";$("ad-count").textContent="—";
    $("release").textContent="Data unavailable";$("status").textContent=`Could not load the map. ${error.message} Use Refresh data to retry.`;
  } finally {$("reload").disabled=false;}
}
$("confidence").addEventListener("input",render);
$("search").addEventListener("input",render);
$("clear").onclick=()=>{selected=null;render();};
$("boundaries").onchange=()=>$("boundaries").checked?boundaries.addTo(map):map.removeLayer(boundaries);
$("basemap").onchange=()=>$("basemap").checked?basemap.addTo(map):map.removeLayer(basemap);
$("reset").onclick=()=>{selected=null;$("search").value="";map.setView([42.697,23.322],12);render();};
$("reload").onclick=load;
basemap.on("tileerror",()=>{basemap.getContainer()?.setAttribute("aria-label","Some basemap tiles unavailable; planning-unit boundaries still work");});
load();

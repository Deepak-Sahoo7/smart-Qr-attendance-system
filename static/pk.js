const CSRF=document.querySelector('meta[name=csrf]').content;
const dec=s=>Uint8Array.from(atob((s+'='.repeat((4-s.length%4)%4)).replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0)).buffer;
const enc=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
async function api(path,body){const r=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF},body:JSON.stringify(body||{})});
  let j;try{j=await r.json()}catch(e){j={ok:false,msg:'Server problem (HTTP '+r.status+'). Try again.'}}j.status=r.status;return j}
function creationOpts(o){o.challenge=dec(o.challenge);o.user.id=dec(o.user.id);(o.excludeCredentials||[]).forEach(c=>c.id=dec(c.id));return o}
function requestOpts(o){o.challenge=dec(o.challenge);(o.allowCredentials||[]).forEach(c=>c.id=dec(c.id));return o}
function cred2json(c){const r=c.response,o={id:c.id,rawId:enc(c.rawId),type:c.type,clientExtensionResults:c.getClientExtensionResults(),response:{clientDataJSON:enc(r.clientDataJSON)}};
  if(r.attestationObject){o.response.attestationObject=enc(r.attestationObject);o.response.transports=r.getTransports?r.getTransports():[]}
  else{o.response.authenticatorData=enc(r.authenticatorData);o.response.signature=enc(r.signature);if(r.userHandle)o.response.userHandle=enc(r.userHandle)}return o}

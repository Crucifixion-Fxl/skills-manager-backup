import { AuthRequiredError, CommandExecutionError, TimeoutError } from '@jackwener/opencli/errors';
const origin = 'https://revenus-sharing-backend.addx.live';
export async function request(path, method = 'GET', payload = {}) {
  const credential = process.env.CONSOLE_TOKEN;
  if (!credential) throw new AuthRequiredError('console.addx.live', 'Complete Console login and inject its native token privately');
  // Keep larger budgets local to exact regional SIM and redeem batch aggregate routes.
  const timeoutSeconds = path === '/revenue/sim/list' ? 35 : path === '/iot-tool/listExchangeCodeDisplay' ? 40 : 15;
  let response;
  try { response = await fetch(origin + path, {method, redirect:'error', headers:{Authorization:credential,'Content-Type':'application/json'}, ...(method==='POST'?{body:JSON.stringify(payload)}:{}),signal:AbortSignal.timeout(timeoutSeconds * 1000)}); }
  catch (e) { if(e.name==='TimeoutError') throw new TimeoutError('Console request',timeoutSeconds); throw new CommandExecutionError('Console request failed'); }
  if ([401,403].includes(response.status)) throw new AuthRequiredError('console.addx.live');
  if (!response.ok) throw new CommandExecutionError(`Console HTTP ${response.status}`);
  let decoded; try { decoded = await response.json(); } catch { throw new CommandExecutionError('Console response is not JSON'); }
  if ([101,50008,50012,50014].includes(decoded.code)) throw new AuthRequiredError('console.addx.live');
  if (decoded.code!==0) throw new CommandExecutionError('Console rejected this read');
  return decoded.data;
}
export async function identity() {
 const record = await request('/user/info','POST');
 if(!record || !Number.isInteger(record.id) || typeof record.email!=='string' || typeof record.userName!=='string') throw new CommandExecutionError('Console identity shape changed');
 if(process.env.CONSOLE_EXPECTED_EMAIL && record.email!==process.env.CONSOLE_EXPECTED_EMAIL) throw new AuthRequiredError('console.addx.live','Target identity mismatch');
 return {userId:record.id,name:record.userName,email:record.email};
}

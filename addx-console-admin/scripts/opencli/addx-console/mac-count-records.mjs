import {ArgumentError,CommandExecutionError} from '@jackwener/opencli/errors';
const labels={0:'Photon Sail',1:'Project B',2:'Project F'};
export function macType(a){if((typeof a.type!=='number'&&typeof a.type!=='string')||! /^[012]$/.test(String(a.type)))throw new ArgumentError('type must be 0 (Photon Sail), 1 (Project B) or 2 (Project F)');return Number(a.type);}
export function macCountRows(d,t){if(!Number.isSafeInteger(d)||d<0)throw new CommandExecutionError('Console MAC available count contract changed');return[{type:t,projectLabel:labels[t],availableCount:d}];}

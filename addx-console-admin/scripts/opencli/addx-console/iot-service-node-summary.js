import {cli,Strategy} from '@jackwener/opencli/registry';
import {identity,request} from './native.mjs';
import {validateNodeSummaryArgs,nodeSummaryRows} from './iot-service-node-summary-records.mjs';
cli({site:'addx-console',name:'iot-service-node-summary',access:'read',strategy:Strategy.LOCAL,browser:false,description:'Read configured environment labels and node counts only; no hosts, node names, SN, device settings or mutations',args:[],columns:['environment','configuredNodeCount'],func:async args=>{validateNodeSummaryArgs(args);await identity();return nodeSummaryRows(await request('/iot/manage/serve/node','POST'));}});

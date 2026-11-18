import schema from './schema.json' with {type:'json'};
const id = (name: keyof typeof schema.objects) => schema.objects[name].id;
export function controlCapabilities(identity: Record<number, unknown>, nativeLimitA: number | null = null) {
  const native5014=nativeLimitA!==null;
  const modes=Number(identity[id('control_modes')]||0),fields=Number(identity[id('feedback_fields')]||0);
  const features=Number(identity[id('optional_features')]||0);
  const min=identity[id('iq_min')],max=identity[id('iq_max')],protection=identity[id('phase_protection_current')],runLimit=identity[id('phase_run_limit_max')];
  const limits=typeof min==='number'&&typeof max==='number'&&typeof protection==='number'&&Number.isFinite(min)&&Number.isFinite(max)&&Number.isFinite(protection)&&typeof runLimit==='number'&&Number.isFinite(runLimit)&&runLimit>0&&min<=0&&max>0&&protection>0&&min<max;
  const watchdog=identity[id('device_watchdog_ms')];
  const compatible=identity[id('dq_current_convention')]===1&&typeof watchdog==='number'&&watchdog>250&&watchdog<=1000;
  const feedback=(fields&20)===20;
  return {
    adapter:native5014?'5014-native':'mbp-1',
    currentLoop:native5014||Boolean(compatible&&limits&&feedback&&(fields&4096)&&(modes&2)),
    dyno:native5014||Boolean(compatible&&limits&&feedback&&(fields&8)&&(modes&32)&&(features&1)&&identity[id('available_iq')]!==undefined&&identity[id('active_limits')]!==undefined),
    currentLoopMinA:native5014?-nativeLimitA:limits?Math.max(min,-protection,-runLimit)/1000:null,
    currentLoopMaxA:native5014?nativeLimitA:limits?Math.min(max,protection,runLimit)/1000:null,
  };
}

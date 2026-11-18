import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server.edge';
import {CharacteristicPlot,characteristicPlotData} from '../web/src/CharacteristicPlot.tsx';

export function renderCharacteristic(run) {
  return renderToStaticMarkup(<CharacteristicPlot loadRun={run} model={characteristicPlotData(run)}/>);
}

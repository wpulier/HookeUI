/** Display reconstruction is separate from scientific resampling. */
export const interpolationModes=['bessel','cubic','linear','blackman','catrom','gaussian','hamming','hanning','hermite','kaiser','lanczos','mitchell','nearest','spline16','spline36'] as const;
export type ImageSampling={interpolation2d:typeof interpolationModes[number]|'custom';interpolation3d:typeof interpolationModes[number];kernel2d:number[][]};
export function imageSampling(raw:unknown):ImageSampling {
 const fail=():never=>{throw new Error('Choose supported image interpolation and a rectangular finite 2D kernel of at most 32 × 32 float32 values. Custom 3D interpolation is not supported by napari.');};
 if(!raw||typeof raw!=='object'||Array.isArray(raw))return fail();
 const v=raw as Record<string,unknown>;
 if(Object.keys(v).sort().join(' ')!=='interpolation2d interpolation3d kernel2d'||![...interpolationModes,'custom'].some(mode=>mode===v.interpolation2d)||!interpolationModes.some(mode=>mode===v.interpolation3d))return fail();
 const k=v.kernel2d;
 if(!Array.isArray(k)||k.length<1||k.length>32||!Array.isArray(k[0])||k[0].length<1||k[0].length>32)return fail();
 const width=k[0].length;
 if(k.some(row=>!Array.isArray(row)||row.length!==width||row.some(n=>typeof n!=='number'||!Number.isFinite(n)||Math.abs(n)>3.4028234663852886e38)))return fail();
 return structuredClone(v) as ImageSampling;
}

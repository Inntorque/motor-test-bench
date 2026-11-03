// Sequence-ordered sensor frames; capacity and cursor semantics match the stream cache.
export class FrameBuffer<Row extends [number, ...unknown[]] = [number, ...number[]]> {
  declare capacity: number;
  declare length: number;
  declare rows: (Row | undefined)[];
  declare start: number;

  constructor(capacity=16000) {
    this.capacity=capacity;this.rows=new Array(capacity);this.start=0;this.length=0;
  }
  at(index: number): Row {return this.rows[(this.start+index)%this.capacity]!;}
  clear() {this.rows.fill(undefined);this.start=0;this.length=0;}
  append(frames: Row[]) {
    for(const frame of frames) {
      if(this.length===this.capacity) {this.rows[this.start]=frame;this.start=(this.start+1)%this.capacity;}
      else {this.rows[(this.start+this.length)%this.capacity]=frame;this.length++;}
    }
  }
  after(sequence: number) {
    let low=0,high=this.length;
    while(low<high) {const mid=(low+high)>>>1;if(this.at(mid)[0]<=sequence)low=mid+1;else high=mid;}
    const result=new Array<Row>(this.length-low);
    for(let i=low;i<this.length;i++)result[i-low]=this.at(i).slice() as Row;
    return result;
  }
}

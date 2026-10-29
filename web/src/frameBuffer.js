// Sequence-ordered sensor frames; capacity and cursor semantics match the stream cache.
export class FrameBuffer {
  constructor(capacity=16000) {
    this.capacity=capacity;this.rows=new Array(capacity);this.start=0;this.length=0;
  }
  at(index) {return this.rows[(this.start+index)%this.capacity];}
  clear() {this.rows.fill(undefined);this.start=0;this.length=0;}
  append(frames) {
    for(const frame of frames) {
      if(this.length===this.capacity) {this.rows[this.start]=frame;this.start=(this.start+1)%this.capacity;}
      else {this.rows[(this.start+this.length)%this.capacity]=frame;this.length++;}
    }
  }
  after(sequence) {
    let low=0,high=this.length;
    while(low<high) {const mid=(low+high)>>>1;if(this.at(mid)[0]<=sequence)low=mid+1;else high=mid;}
    const result=new Array(this.length-low);
    for(let i=low;i<this.length;i++)result[i-low]=this.at(i).slice();
    return result;
  }
}

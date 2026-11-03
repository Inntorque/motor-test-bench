export class DeviceEvent<T> extends MessageEvent<string> {
  readonly detail: T;
  private encoded?: string;

  constructor(type: string, detail: T) {
    super(type);
    this.detail = detail;
  }
  override get data() {
    return this.encoded ??= JSON.stringify({type:this.type,data:this.detail});
  }
}

export function eventData<T = unknown>(event: MessageEvent<string> & {detail?: T}): T {
  return 'detail' in event ? event.detail! : JSON.parse(event.data).data;
}

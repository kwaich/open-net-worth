interface ProtocolRegistry {
  handle(scheme: string, handler: (request: Request) => Response | Promise<Response>): void;
  unhandle(scheme: string): void;
}

export class ArtifactProtocolOwner {
  private registered = false;
  constructor(private readonly protocol: ProtocolRegistry) {}
  register(handler: (request: Request) => Response | Promise<Response>): void {
    this.protocol.handle('onw-artifact', handler);
    this.registered = true;
  }
  retire(): void {
    if (!this.registered) return;
    this.protocol.unhandle('onw-artifact');
    this.registered = false;
  }
}

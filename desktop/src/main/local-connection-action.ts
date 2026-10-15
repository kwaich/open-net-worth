interface LocalConnectionAction {
  select(alternate: boolean): Promise<boolean>;
  showFailure(message: string, offerAlternate: boolean): Promise<boolean>;
}

export async function openLocalConnection(action: LocalConnectionAction): Promise<boolean> {
  try { return await action.select(false); }
  catch (error) {
    const message = error instanceof Error ? error.message : 'The local book could not open. Your current connection has been kept.';
    if (!await action.showFailure(message, true)) return false;
    try { return await action.select(true); }
    catch (failure) {
      await action.showFailure(failure instanceof Error ? failure.message : 'That folder could not open. Your current connection has been kept.', false);
      return false;
    }
  }
}

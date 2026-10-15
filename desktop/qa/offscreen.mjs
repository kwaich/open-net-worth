// QA windows must never appear on the developer's screen: park every window, present and future, far off-screen.
export async function keepOffscreen(app) {
  await app.evaluate(({ app: electronApp, BrowserWindow }) => {
    electronApp.dock?.hide();
    for (const window of BrowserWindow.getAllWindows()) window.setPosition(-3000, -3000);
    electronApp.on("browser-window-created", (_event, window) => window.setPosition(-3000, -3000));
  });
}

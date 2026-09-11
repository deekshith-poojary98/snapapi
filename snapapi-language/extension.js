const vscode = require("vscode");

function activate(context) {
  const disposable = vscode.commands.registerCommand("snapapi.runCurrentFile", async () => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      vscode.window.showWarningMessage("Open a .snaptest file to run SnapAPI.");
      return;
    }
    const file = editor.document.uri.fsPath;
    if (!file.endsWith(".snaptest")) {
      vscode.window.showWarningMessage("SnapAPI can only run .snaptest files.");
      return;
    }
    await editor.document.save();
    const terminal = vscode.window.createTerminal({ name: "SnapAPI" });
    terminal.show();
    terminal.sendText(`snapapi "${file}"`);
  });
  context.subscriptions.push(disposable);
}

function deactivate() {}

module.exports = { activate, deactivate };

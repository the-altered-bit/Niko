// Niko VS Code extension: launches the Niko language server (`niko2 lsp`)
// for editing features and registers the Niko debug adapter
// (`niko2 debug`) for DAP debugging.
const vscode = require('vscode');
const { LanguageClient } = require('vscode-languageclient/node');

let client = null;

function serverCommand(context) {
    const cfg = vscode.workspace.getConfiguration('niko');
    return {
        command: cfg.get('pythonPath', 'python3'),
        args: ['-m', 'niko2', 'lsp'],
        options: { env: { ...process.env } },
    };
}

function activate(context) {
    const serverOptions = serverCommand(context);
    const clientOptions = {
        documentSelector: [{ scheme: 'file', language: 'niko' }],
    };
    client = new LanguageClient('niko', 'Niko Language Server', serverOptions, clientOptions);
    context.subscriptions.push(client.start());

    const debugFactory = {
        createDebugAdapterDescriptor(_session) {
            const cfg = vscode.workspace.getConfiguration('niko');
            const python = cfg.get('pythonPath', 'python3');
            return new vscode.DebugAdapterExecutable(python, ['-m', 'niko2', 'debug']);
        },
    };
    context.subscriptions.push(
        vscode.debug.registerDebugAdapterDescriptorFactory('niko', debugFactory)
    );
}

function deactivate() {
    return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };

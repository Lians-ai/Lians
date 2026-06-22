// Minimal stub for the optional @langchain/core peer dependency.
// This lets the package compile without @langchain/core installed.
// The real types are used at runtime when the package is present.
declare module "@langchain/core/tools" {
  export class DynamicTool {
    constructor(fields: {
      name: string;
      description: string;
      func: (input: string) => Promise<string>;
    });
  }
}

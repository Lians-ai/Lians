/*
 * Grafana supplies these modules at runtime. Keep the small surface used by
 * this plugin typed locally so building the SystemJS bundle does not install
 * an entire host UI release or its unrelated browser dependency graph.
 */
declare module '@grafana/data' {
  import type { ComponentType } from 'react';

  export class AppPlugin {
    addConfigPage(config: {
      title: string;
      body: ComponentType;
      id: string;
    }): this;
  }
}

declare module '@grafana/ui' {
  import type { ComponentType, ReactNode } from 'react';

  export const Alert: ComponentType<{
    title: string;
    severity: 'info' | 'success' | 'warning' | 'error';
    children?: ReactNode;
  }>;
  export const CodeEditor: ComponentType<{
    value: string;
    language: string;
    readOnly?: boolean;
    height?: string;
  }>;
  export const Stack: ComponentType<{
    direction?: 'row' | 'column';
    gap?: number;
    children?: ReactNode;
  }>;
}

/**
 * HTML Report Generator - Creates interactive benchmark reports with circuit visualizations
 */
import { RealBenchmarkSuite } from './benchmark-runner-ollama.js';
import { CircuitState } from './mock-mcp-server.js';
export interface HtmlReportOptions {
    title?: string;
    includeCircuits?: boolean;
    includeDetailed?: boolean;
    theme?: 'light' | 'dark';
}
/**
 * Generate HTML report from benchmark results
 */
export declare function generateHtmlReport(suite: RealBenchmarkSuite, circuits: Map<string, CircuitState>, options?: HtmlReportOptions): string;
//# sourceMappingURL=html-report-generator.d.ts.map
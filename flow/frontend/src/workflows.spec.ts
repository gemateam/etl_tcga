import { describe, it, expect } from "vitest";
import tcgaV3 from "../../workflows/tcga_v3.json";
import gemamxV1 from "../../workflows/gemamx_v1.json";
import gemamxV2 from "../../workflows/gemamx_v2.json";

describe("Smoke test de definiciones de workflows", () => {
  it("carga TCGA v3 con 18 nodos y 20 aristas", () => {
    expect(tcgaV3.id).toBe("tcga_v3");
    expect(tcgaV3.nodes.length).toBe(18);
    expect(tcgaV3.edges.length).toBe(20);
  });

  it("carga GEMAMX v1 con 5 nodos y 3 aristas (ADN puro)", () => {
    expect(gemamxV1.id).toBe("gemamx_v1");
    expect(gemamxV1.nodes.length).toBe(5);
    expect(gemamxV1.edges.length).toBe(3);
  });

  it("carga GEMAMX v2 granular KNIME con 17 nodos y 16 aristas (ADN puro)", () => {
    expect(gemamxV2.id).toBe("gemamx_v2");
    expect(gemamxV2.nodes.length).toBe(17);
    expect(gemamxV2.edges.length).toBe(16);
  });
});


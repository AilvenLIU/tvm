# Program Analysis

<cite>
**Referenced Files in This Document**
- [analysis.h](file://include/tvm/relax/analysis.h)
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [shape_analysis.cc](file://src/relax/analysis/shape_analysis.cc)
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)
- [computable_at_compile_time.cc](file://src/relax/analysis/computable_at_compile_time.cc)
- [udchain.cc](file://src/relax/analysis/udchain.cc)
- [var2value.cc](file://src/relax/analysis/var2value.cc)
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document explains the Relax program analysis capabilities in the TVM codebase, focusing on memory usage estimation, structural equality checking, dependency analysis, and program verification tools. It documents the analysis framework built around visitor patterns and utility functions for program inspection, and shows how these analyses integrate with transformation passes and optimization strategies. Practical examples illustrate debugging, performance profiling, and correctness verification. Guidance is included for scaling analysis to large Relax programs and developing custom analyses.

## Project Structure
The Relax analysis subsystem resides under src/relax/analysis and exposes APIs via include/tvm/relax/analysis.h. Key modules include:
- Variable and binding analysis (free/bound/all variables, use-def chains, var-to-value mapping)
- Structural equality and well-formedness checks
- Shape equality proofs and symbolic variable handling
- Operator pattern classification and reshape detection
- Recursion detection across global functions
- Compile-time computability analysis
- Graph-based operator fusion partitioning

```mermaid
graph TB
A["analysis.h<br/>Public API"] --> B["analysis.cc<br/>VarVisitor, impure call checks"]
A --> C["shape_analysis.cc<br/>CanProveShapeEqual"]
A --> D["struct_info_analysis.cc<br/>StructInfoBaseCheck, EraseToWellDefined"]
A --> E["well_formed.cc<br/>WellFormed checker"]
A --> F["udchain.cc<br/>UDChain, FunctionUseDef"]
A --> G["var2value.cc<br/>AnalyzeVar2Value, NameToBinding"]
A --> H["detect_recursion.cc<br/>DetectRecursion"]
A --> I["tir_op_pattern_kind.cc<br/>AnalyzeOpPatternKind, HasReshapePattern"]
A --> J["graph_partitioner.h/.cc<br/>IndexedForwardGraph, GraphPartitioner"]
```

**Diagram sources**
- [analysis.h](file://include/tvm/relax/analysis.h)
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [shape_analysis.cc](file://src/relax/analysis/shape_analysis.cc)
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)
- [udchain.cc](file://src/relax/analysis/udchain.cc)
- [var2value.cc](file://src/relax/analysis/var2value.cc)
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

**Section sources**
- [analysis.h](file://include/tvm/relax/analysis.h)
- [analysis.cc](file://src/relax/analysis/analysis.cc)

## Core Components
- Variable and binding analysis
  - FreeVars, BoundVars, AllVars, AllGlobalVars
  - AnalyzeVar2Value, NameToBinding
  - FunctionUseDef, DataflowBlockUseDef, CollectVarUsage, GetUsedVars
- Structural equality and well-formedness
  - StructInfoBaseCheck, IsBaseOf, StructInfoLCA, StructInfoBaseCheckPrecondition
  - EraseToWellDefined, GetStaticType, StructInfoFromType
  - WellFormed
- Shape and symbolic variable utilities
  - CanProveShapeEqual
  - TIRVarsInStructInfo, DefinableTIRVarsInStructInfo, FreeSymbolicVars, DefinedSymbolicVars
  - CollectNonNegativeExpressions
- Impurity and purity checks
  - ContainsImpureCall, FindImpureCall
- Operator patterns and reshape detection
  - AnalyzeOpPatternKind, HasReshapePattern
- Recursion detection
  - DetectRecursion
- Compile-time computability
  - ComputableAtCompileTime
- Graph partitioning for fusion
  - IndexedForwardGraph, DominatorTree, GraphPartitioner

**Section sources**
- [analysis.h](file://include/tvm/relax/analysis.h)
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)
- [udchain.cc](file://src/relax/analysis/udchain.cc)
- [var2value.cc](file://src/relax/analysis/var2value.cc)
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)
- [computable_at_compile_time.cc](file://src/relax/analysis/computable_at_compile_time.cc)
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

## Architecture Overview
The analysis framework is visitor-centric:
- ExprVisitor-based visitors traverse Relax IR to collect variable usage, detect recursion, and enforce well-formedness.
- StructInfoFunctor-based visitors implement structural equality and type derivations.
- TIR expression visitors analyze buffer access patterns for operator fusion and reshape detection.
- GraphPartitioner builds an IndexedForwardGraph and computes a post-dominator tree to guide fusion.

```mermaid
classDiagram
class VarVisitor {
+Free(expr) Var[]
+Bound(expr) Var[]
+All(expr) Var[]
+AllGlobalVars(expr) GlobalVar[]
+VisitExpr_(VarNode)
+VisitExpr_(FunctionNode)
+VisitExpr_(CallNode)
+VisitBinding_(VarBindingNode)
+VisitBinding_(MatchCastNode)
}
class StructInfoBaseChecker {
+VisitStructInfo(...)
+VisitStructInfo_(TensorStructInfoNode,...)
+VisitStructInfo_(FuncStructInfoNode,...)
+CombineCheck(...)
+ArrayCheck(...)
}
class WellFormedChecker {
+Check(obj, check_struct_info) bool
+VisitExpr_(FunctionNode)
+VisitExpr_(CallNode)
+VisitExpr_(IfNode)
+VisitBinding_(VarBindingNode)
+VisitBinding_(MatchCastNode)
}
class PatternKindAnalyzer {
+AnalyzeOpPatternKind(func) OpPatternKind
+HasReshapePattern(func) bool
+IsElemwisePattern(...)
+IsBroadcastPattern(...)
+IsInjectivePattern(...)
+IsPureReducePattern(...)
}
class GraphPartitioner {
+Partition(graph) vector~Group*~
+RunFuse(graph, post_dom_tree, phase)
+CheckPath(...)
+CommitFuse(...)
}
VarVisitor <.. WellFormedChecker : "shared IR traversal"
StructInfoBaseChecker <.. WellFormedChecker : "structural checks"
PatternKindAnalyzer <.. GraphPartitioner : "pattern info"
```

**Diagram sources**
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

## Detailed Component Analysis

### Variable and Binding Analysis
- VarVisitor implements collection of free, bound, and global variables, and marks bounded scopes during traversal.
- UDChain computes use-def maps, tracks forward declarations, and extracts outputs.
- Var2Val and NameToBinding map variable definitions to values and group bindings by name.

```mermaid
sequenceDiagram
participant Client as "Caller"
participant UD as "UDChain"
participant EV as "ExprVisitor"
Client->>UD : Collect(expr)
UD->>EV : VisitExpr(expr)
EV-->>UD : VisitBinding_(VarBindingNode)
EV-->>UD : VisitExpr_(VarNode)
UD-->>Client : VarUsageInfo{bound_values, downstream_usage, outputs}
```

**Diagram sources**
- [udchain.cc](file://src/relax/analysis/udchain.cc)

**Section sources**
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [udchain.cc](file://src/relax/analysis/udchain.cc)
- [var2value.cc](file://src/relax/analysis/var2value.cc)

### Structural Equality and Well-Formedness
- StructInfoBaseCheck determines whether a base StructInfo subsumes a derived StructInfo with fine-grained failure modes.
- EraseToWellDefined removes dependencies on undefined symbolic variables, enabling safe propagation across scopes.
- WellFormed enforces IR invariants: variable scoping, struct_info presence, purity constraints, and operator normalization/validation.

```mermaid
flowchart TD
Start(["Start WellFormed"]) --> CheckSI["Check struct_info presence"]
CheckSI --> CheckVars["Check var scoping and definitions"]
CheckVars --> CheckPurity["Validate purity and kForcePure"]
CheckPurity --> CheckNormalize["Validate operator normalization/validate"]
CheckNormalize --> CheckStructInfoEq["Verify StructInfo annotations vs inferred"]
CheckStructInfoEq --> End(["End WellFormed"])
```

**Diagram sources**
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)

**Section sources**
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)

### Shape Equality and Symbolic Variables
- CanProveShapeEqual compares symbolic shapes using integer analysis to decide equality with best-effort guarantees.
- Utilities extract TIR variables from StructInfo and derive non-negativity constraints for shape validity.

```mermaid
flowchart TD
S(["ShapeExpr or Array"]) --> CheckSame["Reference equality?"]
CheckSame --> |Yes| True1["Return true"]
CheckSame --> |No| SizeEq["Size equal?"]
SizeEq --> |No| False1["Return false"]
SizeEq --> |Yes| Loop["For each pair of PrimExpr"]
Loop --> Prove["CanProveEqual(a[i], b[i])?"]
Prove --> |No| False2["Return false"]
Prove --> |Yes| Next["Next pair"]
Next --> Loop
Loop --> Done["Return true"]
```

**Diagram sources**
- [shape_analysis.cc](file://src/relax/analysis/shape_analysis.cc)

**Section sources**
- [shape_analysis.cc](file://src/relax/analysis/shape_analysis.cc)
- [analysis.h](file://include/tvm/relax/analysis.h)

### Operator Patterns and Reshape Detection
- AnalyzeOpPatternKind classifies blocks into elemwise, broadcast, injective, reduce, and out-ewise fusable patterns, guiding fusion.
- HasReshapePattern detects contiguous reshapes by flattening indices and verifying index equality under iteration maps.

```mermaid
flowchart TD
A["Visit S-Blocks"] --> OneStore{"Exactly one BufferStore?"}
OneStore --> |No| Opaque["Mark kOpaque"]
OneStore --> |Yes| InspectLoads["Inspect BufferLoad indices"]
InspectLoads --> Elemwise{"Indices identical?"}
Elemwise --> |Yes| SetEW["kind = max(kind, kElemWise)"]
Elemwise --> |No| Broadcast{"Load indices subset of store?"}
Broadcast --> |Yes| SetBc["kind = max(kind, kBroadcast)"]
Broadcast --> |No| Injective{"All load vars in store vars?"}
Injective --> |Yes| SetInj["kind = max(kind, kInjective)"]
Injective --> |No| Opaque
SetEW --> CheckRed["Has reduction iter vars?"]
SetBc --> CheckRed
SetInj --> CheckRed
CheckRed --> |Yes| CommReduce["kind = max(kind, kCommReduce)"]
CheckRed --> |No| OutEWise{"Is output block?"}
OutEWise --> |Yes| SetOE["kind = kOutEWiseFusable"]
OutEWise --> |No| Final["Done"]
```

**Diagram sources**
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)

**Section sources**
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)

### Recursion Detection
- DetectRecursion constructs a dependency graph over global functions, converts to indexed adjacency, and applies Johnson’s circuit-finding to coalesce mutually recursive groups.

```mermaid
sequenceDiagram
participant M as "IRModule"
participant DG as "DependencyGatherer"
participant Algo as "Johnson SCC/Circuits"
participant Ret as "Result"
M->>DG : Track(Function)
DG-->>M : Set of referenced GlobalVar names
M->>Algo : Build adjacency_index
Algo->>Algo : Find SCCs (Tarjan)
Algo->>Algo : Circuit search (Johnson)
Algo->>Ret : Mutually recursive groups
```

**Diagram sources**
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)

**Section sources**
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)

### Compile-Time Computability
- ComputableAtCompileTime identifies variables whose values can be computed at compile-time given known inputs and symbolic variables.

```mermaid
flowchart TD
Start(["Start Collect"]) --> Params["Mark first N params known"]
Params --> Traverse["Traverse bindings"]
Traverse --> CheckFree["FreeVars(value) ⊆ known?"]
CheckFree --> |Yes| MarkKnown["Mark binding->var known"]
CheckFree --> |No| Skip["Skip"]
MarkKnown --> Propagate["Propagate TIR vars from StructInfo"]
Propagate --> Traverse
Skip --> Traverse
Traverse --> End(["Return known variables"])
```

**Diagram sources**
- [computable_at_compile_time.cc](file://src/relax/analysis/computable_at_compile_time.cc)

**Section sources**
- [computable_at_compile_time.cc](file://src/relax/analysis/computable_at_compile_time.cc)

### Graph Partitioning for Fusion
- IndexedForwardGraph encodes a forward dataflow fragment with node patterns and edges.
- DominatorTree computes post-dominance relations to guide fusion.
- GraphPartitioner merges groups respecting pattern hierarchies and argument limits.

```mermaid
classDiagram
class IndexedForwardGraph {
+Node ref
+size_t index
+bool extern_ref
+OpPatternKind pattern
+LinkedList~Edge~ outputs
}
class DominatorTree {
+PostDom(arena, graph) DominatorTree
-LeastCommonAncestor(...)
-GetNode(...)
}
class GraphPartitioner {
+Partition(graph) vector~Group*~
-InitGroups(graph)
-RunFuse(graph, post_dom_tree, phase)
-CheckPath(...)
-CommitFuse(...)
}
class Group {
+Group* parent
+OpPatternKind pattern
+size_t num_nodes
+size_t args_num
}
GraphPartitioner --> DominatorTree : "uses"
GraphPartitioner --> IndexedForwardGraph : "reads"
GraphPartitioner --> Group : "manages"
```

**Diagram sources**
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

**Section sources**
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

## Dependency Analysis
- Public API surface: include/tvm/relax/analysis.h declares all analysis functions and enumerations.
- Visitor-based modules depend on Relax IR and TIR expression functors.
- Graph partitioner depends on arena utilities and pattern kinds.

```mermaid
graph LR
API["analysis.h"] --> Var["analysis.cc"]
API --> SI["struct_info_analysis.cc"]
API --> WF["well_formed.cc"]
API --> UD["udchain.cc"]
API --> V2V["var2value.cc"]
API --> REC["detect_recursion.cc"]
API --> PAT["tir_op_pattern_kind.cc"]
API --> GP["graph_partitioner.h/.cc"]
PAT --> GP
SI --> WF
Var --> WF
```

**Diagram sources**
- [analysis.h](file://include/tvm/relax/analysis.h)
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)
- [udchain.cc](file://src/relax/analysis/udchain.cc)
- [var2value.cc](file://src/relax/analysis/var2value.cc)
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)
- [tir_op_pattern_kind.cc](file://src/relax/analysis/tir_op_pattern_kind.cc)
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

**Section sources**
- [analysis.h](file://include/tvm/relax/analysis.h)

## Performance Considerations
- Traversal complexity
  - Variable and binding analysis: O(|IR|) with efficient insertion sets and ordered traversals.
  - Well-formedness: O(|IR|) with scope tracking and operator-specific normalization/validation checks.
  - Structural equality: O(|StructInfo|) per comparison with analyzer-based simplifications.
  - Recursion detection: O(V + E) for dependency graph plus Johnson’s algorithm overhead; acceptable for typical module sizes.
  - Operator pattern analysis: O(|S-blocks| + |loads|) per function; early exits on opaque classification.
  - Graph partitioning: O(|nodes| + |edges|) with union-find and path checks; configurable limits prevent excessive fusion.
- Scalability limits
  - Large modules with many global functions and deeply nested expressions may increase recursion detection and fusion computation time.
  - Shape equality relies on integer analysis; complex symbolic expressions can slow down proof attempts.
- Best practices
  - Normalize IR before structural checks to minimize redundant work.
  - Use targeted analysis passes (e.g., shape equality only when needed).
  - Limit fusion depth and argument counts to maintain executable size and runtime performance.
  - Prefer incremental analysis updates when transforming IR to avoid recomputation.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- Well-formedness warnings
  - Missing struct_info, invalid variable scoping, purity violations, or operator normalization errors are logged as warnings. Review the reported expressions and fix annotations or normalization.
- Impure call detection
  - ContainsImpureCall and FindImpureCall help locate side-effecting operations. Ensure purity annotations align with semantics or adjust purity attributes accordingly.
- Structural equality failures
  - StructInfoBaseCheck returns fine-grained failure modes. Use StructInfoBaseCheckPrecondition to derive necessary conditions for equality.
- Recursion issues
  - DetectRecursion reports mutually recursive groups. Break cycles or refactor to reduce interdependencies.
- Operator fusion pitfalls
  - If fusion yields oversized functions, lower max_fuse_depth or max_function_args in GraphPartitioner configuration.

**Section sources**
- [well_formed.cc](file://src/relax/analysis/well_formed.cc)
- [analysis.cc](file://src/relax/analysis/analysis.cc)
- [struct_info_analysis.cc](file://src/relax/analysis/struct_info_analysis.cc)
- [detect_recursion.cc](file://src/relax/analysis/detect_recursion.cc)
- [graph_partitioner.h](file://src/relax/analysis/graph_partitioner.h)
- [graph_partitioner.cc](file://src/relax/analysis/graph_partitioner.cc)

## Conclusion
The Relax analysis toolkit provides a robust, visitor-driven framework for correctness, performance, and optimization. It supports structural equality, well-formedness, variable and binding analysis, recursion detection, operator pattern classification, and graph-based fusion. Together, these capabilities enable effective debugging, profiling, and verification of Relax programs, and integrate naturally with transformation passes to drive analysis-aware optimizations.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Practical Examples
- Debugging variable scoping
  - Use FreeVars and BoundVars to inspect variable lifetimes in a function body.
  - Use AnalyzeVar2Value to map definitions to their values and NameToBinding to group bindings by variable name.
- Correctness verification
  - Run WellFormed to validate IR invariants and StructInfoBaseCheck to confirm structural compatibility.
  - Use ContainsImpureCall to ensure purity constraints are met.
- Performance profiling
  - Compute operator patterns with AnalyzeOpPatternKind and detect reshapes with HasReshapePattern to guide layout and memory access optimizations.
- Optimization strategies
  - Use DetectRecursion to inform scheduling and kernel splitting.
  - Apply GraphPartitioner to fuse compatible operators while respecting depth and argument limits.
  - Identify compile-time computable variables with ComputableAtCompileTime to hoist constants.

[No sources needed since this section provides general guidance]
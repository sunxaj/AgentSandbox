# AI Agent Architectures: LangGraph vs. Agentic Harnesses

## 1. A Simple LangGraph Example: The Auto-Fix & Deploy Pipeline

Imagine you want an AI to write a feature, test it, get human approval, and deploy it. **LangGraph** orchestrates this using a **State, Nodes (Steps), and Edges (Logic)**:

```
                  +-------------------+
                  |   1. Write Code   |
                  +---------+---------+
                            |
                            v
                  +-------------------+
                  |    2. Run Tests   |
                  +---------+---------+
                            |
             (Pass)         v         (Fail)
       +--------------------+--------------------+
       |                                         |
       v                                         v
+--------------+                         +---------------+
| 3. Ask Human |                         | 4. Fix Agent  |
+------+-------+                         +-------+-------+
       |                                         |
 (Approve)                                       | (Loop Back)
       v                                         v
+--------------+                                 |
|  5. Deploy   | <-------------------------------+
+--------------+
```

* **The Shared State:** A central dictionary keeps track of everything: `{"code": "...", "test_results": "...", "approved": False}`. Every step reads from and writes to this State.
* **Deterministic Steps (Nodes):** 
  * **Node A (Coder Agent):** Takes a prompt, writes code, and saves it to the State.
  * **Node B (Test Runner):** A purely Python-coded step (no AI) that runs `pytest`. It saves the pass/fail result to the State.
* **Branching Logic (Conditional Edges):** LangGraph looks at the State after the Test Runner finishes. 
  * If tests *fail*, the graph routes the workflow to **Node C (Fix Agent)**.
  * If tests *pass*, it routes the workflow to **Node D (Human Approval)**.
* **Human-in-the-Loop Interrupt:** When the graph hits the Human Approval node, LangGraph completely **pauses and persists the state** to a database. It waits until a human clicks "Approve" via a UI, updates the State to `approved: True`, and resumes the graph.
* **Multi-Agent Routing:** If the Fix Agent fails three times, an edge can route the state away from the coding agents entirely and pass it to a **Senior Reviewer Agent** node with a different prompt and higher permissions.

---

## 2. The Core Difference: Native Harness vs. State-Machine

While both architectures can ask for user approval, they approach the entire execution problem from opposite philosophies.

| Feature | Common Agentic Harness (The Autonomous Loop) | LangGraph State-Machine (The Structured Track) |
| :--- | :--- | :--- |
| **Analogy** | A **Self-Driving Car**. It has a destination, but it decides lane changes, braking, and steering dynamically in real-time. | A **Train Grid**. The train can switch tracks, stop at stations, or reverse, but it *must* follow the pre-laid steel rails. |
| **Control Flow** | **Dynamic & LLM-Driven.** The LLM decides what to do next based on its previous thought. The harness just obeys. | **Deterministic & Developer-Driven.** The developer defines the paths. The LLM only makes decisions *inside* allowed boxes. |
| **Human Approval Mechanism** | **An Intercept Tool.** The LLM encounters a protected tool (e.g., `delete_database()`). The harness pauses the loop, asks the user, and resumes the *same continuous loop*. | **A Graph State Breakpoint.** The workflow explicitly ends at a "Human Node". The entire program spins down, saves to a database, and can be resumed days later by a completely different process. |
| **Predictability** | **Low.** The LLM might take 3 steps or 50 steps, and it might hallucinate a tool it doesn't have. | **High.** You know exactly which nodes the data passed through because LangGraph forces it along defined edges. |
| **Best Used For** | Open-ended, unpredictable engineering tasks (e.g., *"Find the bug in this repo and fix it"*). | Strict, compliance-heavy business workflows (e.g., *"Draft invoice ➔ Audit ➔ Manager Approval ➔ Wire Transfer"*). |

### Summary of the Approval Difference

* **In a Harness:** Human approval is just a speed bump. The AI is driving, hits a "stop sign" (the harness prompting the user), gets permission, and keeps driving in the same session.
* **In a State-Machine (LangGraph):** Human approval is a train station. The workflow arrives, passengers get off, the engine shuts down, and the exact state is frozen in a database. When a human eventually approves it, a brand-new engine starts up, reads the frozen state, and moves the train to the next track.
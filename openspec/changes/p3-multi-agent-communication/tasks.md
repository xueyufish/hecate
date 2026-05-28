## 1. Agent Message Bus

- [ ] 1.1 Create `services/multi_agent/message_bus.py` with AgentMessageBus class
- [ ] 1.2 Implement publish(topic, message) — publish message to topic
- [ ] 1.3 Implement subscribe(topic, agent_id) — subscribe agent to topic
- [ ] 1.4 Implement direct_message(from_agent, to_agent, message) — point-to-point messaging
- [ ] 1.5 Implement broadcast(from_agent, message) — broadcast to all agents

## 2. Dynamic Task Allocation

- [ ] 2.1 Create `services/multi_agent/task_allocator.py` with DynamicTaskAllocator class
- [ ] 2.2 Implement route(task_description, available_agents) — LLM-driven routing
- [ ] 2.3 Implement load_balancer — consider agent load in routing decisions
- [ ] 2.4 Implement fallback — default routing when LLM cannot determine

## 3. P2P Negotiation

- [ ] 3.1 Create `services/multi_agent/negotiator.py` with P2PNegotiator class
- [ ] 3.2 Implement negotiate(task, agents) — multi-round negotiation protocol
- [ ] 3.3 Implement timeout handling — escalate to coordinator on timeout

## 4. Testing

- [ ] 4.1 Unit tests for AgentMessageBus — publish, subscribe, direct, broadcast
- [ ] 4.2 Unit tests for DynamicTaskAllocator — routing, load balancing, fallback
- [ ] 4.3 Unit tests for P2PNegotiator — negotiation, timeout

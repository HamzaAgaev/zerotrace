import type { RouteWork, CallConfig, LabFault } from "./api/types.js";
export interface LabScenario { schema_version: "1"; scenario_id: string; scenario_revision: number; entrypoint: {service_id:string;route:string}; services: Record<string,{config_write?:boolean; routes:Record<string,{fault?:Omit<LabFault,'service_id'|'route'>|null;work:RouteWork;response_status:number;tail_ms?:number;calls:Record<string,Omit<CallConfig,"call_ref"|"write_fields"> & {required:boolean}>;steps:{id:string;call?:string;parallel?:string[];delay_ms?:number;offsets_ms?:Record<string,number>}[]}>}> }
export const exampleScenario: LabScenario = {
  "schema_version": "1",
  "scenario_id": "example",
  "scenario_revision": 1,
  "entrypoint": {
    "service_id": "api",
    "route": "POST /run"
  },
  "services": {
    "api": {
      "config_write": true,
      "routes": {
        "POST /run": {
          "work": {
            "kind": "fixed",
            "delay_ms": 10
          },
          "response_status": 200,
          "calls": {
            "first": {
              "target": {
                "service_id": "worker-a",
                "method": "GET",
                "route": "/work"
              },
              "timeout_ms": 500,
              "max_attempts": 2,
              "backoff_ms": 20,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "second": {
              "target": {
                "service_id": "worker-b",
                "method": "GET",
                "route": "/work"
              },
              "timeout_ms": 500,
              "max_attempts": 2,
              "backoff_ms": 20,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": false
            }
          },
          "steps": [
            {
              "id": "parallel-work",
              "parallel": [
                "first",
                "second"
              ]
            },
            {
              "id": "repeat-first",
              "call": "first"
            }
          ]
        }
      }
    },
    "worker-a": {
      "config_write": true,
      "routes": {
        "GET /work": {
          "work": {
            "kind": "fixed",
            "delay_ms": 10
          },
          "response_status": 200,
          "calls": {},
          "steps": []
        }
      }
    },
    "worker-b": {
      "config_write": true,
      "routes": {
        "GET /work": {
          "work": {
            "kind": "fixed",
            "delay_ms": 10
          },
          "response_status": 503,
          "calls": {},
          "steps": []
        }
      }
    }
  }
};
export function parseScenario(text:string):LabScenario {
 let value:LabScenario; try {value=JSON.parse(text);} catch {throw new Error("Некорректный JSON. Проверьте запятые и кавычки.");}
 if(!value || value.schema_version!=="1" || !value.scenario_id || !value.entrypoint || !value.services || !Object.keys(value.services).length) throw new Error("Укажите schema_version, scenario_id, scenario_revision, entrypoint и services.");
 if(!Number.isInteger(value.scenario_revision)||value.scenario_revision<1)throw new Error('scenario_revision должен быть положительным целым числом.');
 const graph=new Map<string,string[]>();
 for(const [id,service] of Object.entries(value.services)) {
   if(!service?.routes||!Object.keys(service.routes).length)throw new Error(`${id}: укажите routes.`);
   for(const [route,definition] of Object.entries(service.routes)) {
     if(!definition?.work||!definition.calls||!Array.isArray(definition.steps)||!Number.isInteger(definition.response_status))throw new Error(`${id} ${route}: укажите work, response_status, calls и steps.`);
     const refs=definition.steps.flatMap(step=>step?.parallel??(step?.call?[step.call]:[]));
     if(refs.some(ref=>!definition.calls[ref])||Object.keys(definition.calls).some(ref=>!refs.includes(ref)))throw new Error(`${id} ${route}: проверьте ссылки calls в steps.`);
     const targets=Object.values(definition.calls).map(call=>{
       if(!call?.target||!value.services[call.target.service_id]?.routes[`${call.target.method} ${call.target.route}`])throw new Error(`${id}: целевой эндпоинт не существует.`);
       if(!Number.isFinite(call.timeout_ms)||call.timeout_ms<=0||!Number.isInteger(call.max_attempts)||call.max_attempts<1||!Array.isArray(call.retry_on)||typeof call.required!=='boolean')throw new Error(`${id}: явно задайте политику повторов и required.`);
       return `${call.target.service_id} ${call.target.method} ${call.target.route}`;
     });
     graph.set(`${id} ${route}`,targets);
   }
 }
 if(!graph.has(`${value.entrypoint.service_id} ${value.entrypoint.route}`))throw new Error('entrypoint: эндпоинт не найден.');
 const visited=new Set<string>(),active=new Set<string>();
 function visit(key:string){if(active.has(key))throw new Error('Циклические вызовы не поддерживаются.');if(visited.has(key))return;active.add(key);for(const target of graph.get(key)??[])visit(target);active.delete(key);visited.add(key);}
 for(const key of graph.keys())visit(key);
 return value;
}

/** Built-in example, kept in sync with stand/scenarios/checkout.json. */
export const checkoutScenario: LabScenario = {
  "schema_version": "1",
  "scenario_id": "checkout",
  "scenario_revision": 1,
  "entrypoint": {
    "service_id": "gateway",
    "route": "POST /checkout"
  },
  "services": {
    "gateway": {
      "config_write": false,
      "routes": {
        "GET /product": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 3,
            "p99_ms": 8
          },
          "calls": {
            "inventory-stock": {
              "target": {
                "service_id": "inventory",
                "method": "GET",
                "route": "/stock"
              },
              "timeout_ms": 250,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "pricing-quote": {
              "target": {
                "service_id": "pricing",
                "method": "GET",
                "route": "/quote"
              },
              "timeout_ms": 250,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            }
          },
          "steps": [
            {
              "id": "product-data",
              "parallel": [
                "inventory-stock",
                "pricing-quote"
              ]
            }
          ],
          "response_status": 200
        },
        "POST /checkout": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 5,
            "p99_ms": 15
          },
          "calls": {
            "orders-create": {
              "target": {
                "service_id": "orders",
                "method": "POST",
                "route": "/orders"
              },
              "timeout_ms": 700,
              "max_attempts": 1,
              "backoff_ms": 50,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            }
          },
          "steps": [
            {
              "id": "order",
              "call": "orders-create"
            }
          ],
          "response_status": 200
        },
        "POST /refund": {
          "work": {
            "kind": "fixed",
            "delay_ms": 4
          },
          "calls": {
            "orders-refund": {
              "target": {
                "service_id": "orders",
                "method": "POST",
                "route": "/refund"
              },
              "timeout_ms": 1000,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx"
              ],
              "required": true
            }
          },
          "steps": [
            {
              "id": "refund",
              "call": "orders-refund"
            }
          ],
          "response_status": 200
        }
      }
    },
    "orders": {
      "config_write": true,
      "routes": {
        "POST /orders": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 10,
            "p99_ms": 30
          },
          "calls": {
            "inventory-stock": {
              "target": {
                "service_id": "inventory",
                "method": "GET",
                "route": "/stock"
              },
              "timeout_ms": 500,
              "max_attempts": 2,
              "backoff_ms": 50,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "pricing-quote": {
              "target": {
                "service_id": "pricing",
                "method": "GET",
                "route": "/quote"
              },
              "timeout_ms": 300,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "delivery-quote": {
              "target": {
                "service_id": "delivery",
                "method": "GET",
                "route": "/quote"
              },
              "timeout_ms": 350,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "charge-payment": {
              "target": {
                "service_id": "payment",
                "method": "POST",
                "route": "/charge"
              },
              "timeout_ms": 250,
              "max_attempts": 3,
              "backoff_ms": 50,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "notify-order": {
              "target": {
                "service_id": "notifications",
                "method": "POST",
                "route": "/order-created"
              },
              "timeout_ms": 250,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx"
              ],
              "required": false
            }
          },
          "steps": [
            {
              "id": "prepare",
              "parallel": [
                "inventory-stock",
                "pricing-quote",
                "delivery-quote"
              ]
            },
            {
              "id": "payment",
              "call": "charge-payment"
            },
            {
              "id": "notification",
              "call": "notify-order"
            }
          ],
          "response_status": 200
        },
        "POST /refund": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 8,
            "p99_ms": 20
          },
          "calls": {
            "refund-payment": {
              "target": {
                "service_id": "payment",
                "method": "POST",
                "route": "/refund"
              },
              "timeout_ms": 700,
              "max_attempts": 2,
              "backoff_ms": 50,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "notify-refund": {
              "target": {
                "service_id": "notifications",
                "method": "POST",
                "route": "/refund-created"
              },
              "timeout_ms": 250,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx"
              ],
              "required": false
            }
          },
          "steps": [
            {
              "id": "refund",
              "call": "refund-payment"
            },
            {
              "id": "notification",
              "call": "notify-refund"
            }
          ],
          "response_status": 200
        }
      }
    },
    "inventory": {
      "config_write": false,
      "routes": {
        "GET /stock": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 10,
            "p99_ms": 25
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        }
      }
    },
    "pricing": {
      "config_write": false,
      "routes": {
        "GET /quote": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 20,
            "p99_ms": 50
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        }
      }
    },
    "delivery": {
      "config_write": false,
      "routes": {
        "GET /quote": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 15,
            "p99_ms": 45
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        }
      }
    },
    "payment": {
      "config_write": true,
      "routes": {
        "POST /charge": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 20,
            "p99_ms": 60
          },
          "calls": {
            "fraud-score": {
              "target": {
                "service_id": "fraud",
                "method": "POST",
                "route": "/score"
              },
              "timeout_ms": 300,
              "max_attempts": 1,
              "backoff_ms": 0,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            },
            "bank-authorize": {
              "target": {
                "service_id": "bank-api",
                "method": "POST",
                "route": "/authorize"
              },
              "timeout_ms": 400,
              "max_attempts": 2,
              "backoff_ms": 40,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            }
          },
          "steps": [
            {
              "id": "fraud",
              "call": "fraud-score"
            },
            {
              "id": "bank",
              "call": "bank-authorize"
            }
          ],
          "response_status": 200
        },
        "POST /refund": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 18,
            "p99_ms": 55
          },
          "calls": {
            "bank-refund": {
              "target": {
                "service_id": "bank-api",
                "method": "POST",
                "route": "/refund"
              },
              "timeout_ms": 500,
              "max_attempts": 2,
              "backoff_ms": 40,
              "retry_on": [
                "5xx",
                "timeout"
              ],
              "required": true
            }
          },
          "steps": [
            {
              "id": "bank",
              "call": "bank-refund"
            }
          ],
          "response_status": 200
        }
      }
    },
    "fraud": {
      "config_write": false,
      "routes": {
        "POST /score": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 15,
            "p99_ms": 40
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        }
      }
    },
    "bank-api": {
      "config_write": false,
      "routes": {
        "POST /authorize": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 50,
            "p99_ms": 120
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        },
        "POST /refund": {
          "work": {
            "kind": "lognormal",
            "p50_ms": 45,
            "p99_ms": 110
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        }
      }
    },
    "notifications": {
      "config_write": false,
      "routes": {
        "POST /order-created": {
          "work": {
            "kind": "fixed",
            "delay_ms": 8
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        },
        "POST /refund-created": {
          "work": {
            "kind": "fixed",
            "delay_ms": 8
          },
          "calls": {},
          "steps": [],
          "response_status": 200
        }
      }
    }
  }
};
